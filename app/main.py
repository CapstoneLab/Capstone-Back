import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select, update

from app.api.repos_router import router as repos_router
from app.auth.jwt_utils import get_current_user
from app.auth.router import router as auth_router
from app.db import Base, SessionLocal, engine
from app.db_models import (  # noqa: F401
    ApprovalRecord,
    BuildArtifact,
    Deployment,
    PipelineJob,
    PipelineStep,
    SecurityFinding,
    SecuritySummary,
    StepLog,
    User,
)
from app.models import (
    PipelineResultPayload,
    PipelineResultResponse,
    StartPipelineRequest,
    StartPipelineResponse,
)
from app.config import get_settings
from app.service import ResultStore, TriggerService, UbuntuResultFetcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("[startup] DB connected, tables ensured.")
    except Exception as exc:
        print(
            f"[startup] WARNING: DB init skipped ({exc.__class__.__name__}: {exc}). "
            "Server will run, but /auth/* endpoints will fail until DB is reachable."
        )
    yield


app = FastAPI(
    title="Windows CI Trigger Backend",
    version="1.0.0",
    lifespan=lifespan,
    root_path=os.getenv("ROOT_PATH", ""),
)

# Cloudflare / 리버스프록시 X-Forwarded-* 헤더 신뢰
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

def _get_allowed_origins() -> list[str]:
    """ALLOWED_ORIGINS 환경변수(콤마구분) + 기본 허용 목록."""
    base = [
        "https://api.pwd.kr",
        "https://pwd.kr",
    ]
    extra = os.getenv("ALLOWED_ORIGINS", "")
    if extra:
        base += [o.strip() for o in extra.split(",") if o.strip()]
    return base

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    """에러 응답을 프론트 명세 형식으로 통일: {error, detail, message}"""
    detail = exc.detail
    if isinstance(detail, dict):
        # 409 등 detail이 dict인 경우: message 필드를 별도로 꺼내고 나머지는 그대로 전파
        message = detail.get("message", str(detail))
        content = {"error": _status_to_error_code(exc.status_code), "message": message, **detail}
    else:
        content = {"error": _status_to_error_code(exc.status_code), "detail": detail, "message": detail}
    return JSONResponse(status_code=exc.status_code, content=content)


def _status_to_error_code(code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        500: "INTERNAL_SERVER_ERROR",
        502: "BAD_GATEWAY",
        504: "GATEWAY_TIMEOUT",
    }.get(code, "ERROR")


app.include_router(auth_router)
app.include_router(repos_router)

trigger_service = TriggerService()
result_store = ResultStore()
result_fetcher = UbuntuResultFetcher()
job_state: dict[str, dict] = {}
# job_id -> list of completed step dicts (populated by step_complete callbacks)
job_steps: dict[str, list[dict]] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/start-pipeline", response_model=StartPipelineResponse)
async def start_pipeline(
    req: StartPipelineRequest,
    current_user: User = Depends(get_current_user),
) -> StartPipelineResponse:
    job_id = str(uuid4())
    now = datetime.now(timezone.utc)

    try:
        async with SessionLocal() as session:
            job = PipelineJob(
                job_id=job_id,
                repo_url=str(req.repo_url),
                branch=req.branch,
                trigger_source=req.trigger_source,
                status="queued",
                source=req.source,
                environment=req.environment,
                workflow_path=req.workflow_path,
                selected_items=req.selected_items or [],
                commit_sha=req.commit_sha,
                created_at=now,
                user_id=current_user.id,
            )
            session.add(job)
            await session.commit()
            print(f"[DB] pipeline_jobs INSERT (queued): {job_id}")
    except Exception as exc:
        print(f"[DB] pipeline_jobs INSERT failed: {exc}")

    job_state[job_id] = {
        "status": "queued",
        "repo_url": str(req.repo_url),
        "branch": req.branch,
        "requested_at": now.isoformat(),
    }

    return StartPipelineResponse(
        job_id=job_id,
        status="queued",
        message="파이프라인이 큐에 등록되었습니다. 엔진이 곧 가져갑니다.",
    )


@app.post("/get-results")
async def receive_result(payload: PipelineResultPayload) -> dict[str, str]:
    obj = payload.model_dump(mode="json")
    cb_type = payload.callback_type
    print(f"[DEBUG callback] job_id={payload.job_id} type={cb_type} effective_status={payload.effective_status} step_name={payload.step.get('name', 'N/A')}")

    # DB에 job이 없으면 자동 생성 (외부에서 직접 콜백 온 경우)
    await _ensure_job_exists(payload)

    if cb_type == "step_complete":
        step_data = payload.step or {}
        if not step_data and obj.get("steps"):
            step_data = obj["steps"][-1] if obj["steps"] else {}
        if step_data:
            job_steps.setdefault(payload.job_id, []).append(step_data)

        # DB에 pipeline_steps INSERT
        await _save_step_to_db(payload.job_id, step_data)

        existing = job_state.get(payload.job_id, {})
        existing.update(
            {
                "status": "running",
                "repo_url": payload.repo_url,
                "branch": payload.branch,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        job_state[payload.job_id] = existing
        return {"message": "step recorded"}

    # pipeline_complete: final callback
    if not obj.get("steps"):
        run_id = (obj.get("metadata") or {}).get("run_id", "")
        steps = result_fetcher.fetch_steps(run_id=run_id)
        if steps:
            obj["steps"] = steps

    result_store.save(payload.job_id, obj)

    # DB에 pipeline_jobs UPDATE (최종 상태)
    await _finalize_job_in_db(payload, obj)

    # 로그 파싱 → security_findings, build_artifacts, step_logs, security_summary 저장
    await _save_parsed_data_to_db(payload.job_id, obj)

    existing = job_state.get(payload.job_id, {})
    existing.update(
        {
            "status": payload.effective_status,
            "repo_url": payload.repo_url,
            "branch": payload.branch,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    job_state[payload.job_id] = existing

    # Clean up step tracking
    job_steps.pop(payload.job_id, None)

    return {"message": "result stored"}


async def _ensure_job_exists(payload: PipelineResultPayload) -> None:
    """DB에 해당 job_id가 없으면 INSERT."""
    try:
        async with SessionLocal() as session:
            existing = await session.get(PipelineJob, payload.job_id)
            if not existing:
                now = datetime.now(timezone.utc)
                started_at = payload.started_at or now
                if started_at < now:
                    started_at = now
                job = PipelineJob(
                    job_id=payload.job_id,
                    repo_url=payload.repo_url,
                    branch=payload.branch,
                    trigger_source="callback",
                    status="running",
                    started_at=started_at,
                    created_at=now,
                )
                session.add(job)
                await session.commit()
                print(f"[DB] pipeline_jobs auto-created: {payload.job_id}")
    except Exception as exc:
        print(f"[DB] _ensure_job_exists failed: {exc}")


async def _save_step_to_db(job_id: str, step_data: dict) -> None:
    """step_complete 콜백 데이터를 pipeline_steps에 INSERT."""
    if not step_data:
        return

    try:
        async with SessionLocal() as session:
            # 우분투 러너: step_name / name 둘 다 지원
            step_name = step_data.get("step_name") or step_data.get("name", "unknown")
            step_type = step_data.get("step_type", step_name)
            step_status = step_data.get("status", "pending")
            started = _parse_time_safe(step_data.get("started_at"))
            ended = _parse_time_safe(step_data.get("finished_at") or step_data.get("ended_at"))
            duration = step_data.get("duration_secs") or step_data.get("duration")
            error_msg = step_data.get("error_message") if step_status == "failed" else None

            # 이미 같은 job_id + step_name이 있으면 중복 INSERT 방지
            existing = await session.execute(
                select(PipelineStep.step_id)
                .where(PipelineStep.job_id == job_id, PipelineStep.step_name == step_name)
            )
            if existing.first():
                return

            step = PipelineStep(
                job_id=job_id,
                step_name=step_name,
                step_type=step_type,
                status=step_status,
                error_message=error_msg,
                started_at=started,
                ended_at=ended,
                duration_secs=float(duration) if duration else None,
                metadata_=step_data.get("metadata", {}),
            )
            session.add(step)
            await session.commit()
            print(f"[DB] pipeline_steps INSERT: {job_id} / {step_name} = {step_status}")
    except Exception as exc:
        print(f"[DB] _save_step_to_db failed: {exc}")


async def _finalize_job_in_db(payload: PipelineResultPayload, obj: dict) -> None:
    """pipeline_complete 시 pipeline_jobs 상태를 최종 업데이트하고, 아직 DB에 없는 steps도 저장."""
    try:
        async with SessionLocal() as session:
            now = datetime.now(timezone.utc)
            final_status = payload.effective_status
            overall = "success" if final_status == "success" else "failed"

            # 시작/종료 시간 계산
            # started_at은 DB의 created_at보다 이전일 수 없으므로 job을 먼저 조회
            job = await session.get(PipelineJob, payload.job_id)
            created_at = job.created_at if job else now

            started = payload.started_at
            if started and started < created_at:
                started = created_at
            ended = payload.ended_at or now
            duration = None
            if started and ended:
                duration = int((ended - started).total_seconds())

            await session.execute(
                update(PipelineJob)
                .where(PipelineJob.job_id == payload.job_id)
                .values(
                    status=final_status,
                    overall_result=overall,
                    started_at=started,
                    completed_at=ended,
                    duration_secs=duration,
                    metadata_=obj.get("metadata", {}),
                )
            )
            await session.commit()
            print(f"[DB] pipeline_jobs UPDATE: {payload.job_id} -> {final_status}")

            # obj["steps"]에 있지만 DB에 아직 없는 step들 저장
            for s in obj.get("steps", []):
                await _save_step_to_db(payload.job_id, s)

    except Exception as exc:
        print(f"[DB] _finalize_job_in_db failed: {exc}")


# ── 구조화된 데이터 → 나머지 테이블 저장 ────────────────────────────────────


def _parse_time_safe(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


async def _save_parsed_data_to_db(job_id: str, obj: dict) -> None:
    """pipeline_complete 시 steps[].metadata.findings 등 구조화된 데이터를 DB에 저장."""
    logs = obj.get("logs", [])
    steps_data = obj.get("steps", [])

    try:
        async with SessionLocal() as session:
            # step_name → step_id 매핑 가져오기
            result = await session.execute(
                select(PipelineStep.step_id, PipelineStep.step_name)
                .where(PipelineStep.job_id == job_id)
            )
            step_map = {row.step_name: row.step_id for row in result.all()}

            # ── 1) step_logs: 로그 라인 개별 저장 ──
            await _save_log_lines(session, job_id, logs, step_map)

            # ── 2) security_findings ──
            gitleaks_count = 0
            semgrep_count = 0
            sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

            gitleaks_step_id = step_map.get("lightweight-security")
            semgrep_step_id = step_map.get("deep-security")

            # 엔진이 security.findings를 구조화 JSON으로 보내면 그걸 우선 사용
            security_block = obj.get("security", {})
            structured_findings = security_block.get("findings", [])

            if structured_findings:
                # ── 구조화된 findings 사용 (엔진 신규 포맷) ──
                for f in structured_findings:
                    scanner = f.get("scanner_name", "semgrep")
                    scan_type = "gitleaks" if scanner == "gitleaks" else "semgrep"
                    step_id = gitleaks_step_id if scan_type == "gitleaks" else semgrep_step_id
                    if not step_id:
                        continue

                    severity = _normalize_severity(f.get("severity", "medium"))
                    if scan_type == "gitleaks":
                        gitleaks_count += 1
                    else:
                        semgrep_count += 1
                    sev_counts[severity] = sev_counts.get(severity, 0) + 1

                    # AI 권고사항: 콜백에서 온 것 우선, 없으면 DB에서 재사용
                    ai_fix = f.get("ai_recommendation")
                    if not ai_fix:
                        existing = await session.execute(
                            select(SecurityFinding.ai_fix_suggestion)
                            .where(
                                SecurityFinding.rule_id == f.get("rule_id", ""),
                                SecurityFinding.file_path == f.get("file_path", ""),
                                SecurityFinding.line_number == f.get("line_number", 0),
                                SecurityFinding.ai_fix_suggestion.isnot(None),
                            )
                            .limit(1)
                        )
                        row = existing.first()
                        if row and row[0]:
                            ai_fix = row[0]

                    # code_snippet: 취약점 줄 기준 앞뒤 2줄(총 5줄)로 트림
                    raw_snippet = f.get("code_snippet")
                    trimmed_snippet, trimmed_start = _trim_snippet(
                        raw_snippet, f.get("line_number", 0), f.get("code_snippet_start_line", 1)
                    )

                    session.add(SecurityFinding(
                        job_id=job_id,
                        step_id=step_id,
                        scan_type=scan_type,
                        severity=severity,
                        rule_id=f.get("rule_id", "unknown"),
                        rule_name=f.get("title") or f.get("rule_id", ""),
                        file_path=f.get("file_path", ""),
                        line_number=f.get("line_number", 0),
                        column_number=f.get("column_number"),
                        message=(f.get("message", "") or "")[:2000],
                        is_masked=scan_type == "gitleaks",
                        ai_fix_suggestion=ai_fix,
                        code_snippet=trimmed_snippet,
                        cwe_id=f.get("cwe"),
                        policy_item=f.get("policy_item"),
                        in_scope=f.get("in_scope", True),
                    ))

            else:
                # ── Fallback: 로그 텍스트에서 regex 파싱 (이전 엔진 호환) ──
                re_gitleaks = re.compile(
                    r"\s*\[(\d+)\]\s+rule=([^\s|]+)\s*\|\s*([^:]+):(\d+)\s*\|\s*(.*)"
                )
                re_semgrep = re.compile(
                    r"\s*\[(\d+)\]\s+\[(\w+)\]\s+([^\s|]+)\s*\|\s*([^:]+):(\d+)\s*\|\s*(.*)"
                )
                re_aifix = re.compile(r"\s*\[AI-FIX\]\s*(.*)")

                parsed_findings: list[dict] = []
                for i, line in enumerate(logs):
                    if "[lightweight-security.log]" in line and gitleaks_step_id:
                        content_match = re.match(r"^\[lightweight-security\.log\]\s*(?:\[[^\]]*\]\s*)?(.*)", line)
                        if not content_match:
                            continue
                        content = content_match.group(1)
                        m = re_gitleaks.match(content)
                        if m:
                            parsed_findings.append({
                                "scan_type": "gitleaks", "step_id": gitleaks_step_id,
                                "severity": "high", "rule_id": m.group(2),
                                "file_path": m.group(3).strip(), "line_number": int(m.group(4)),
                                "message": m.group(5).strip(), "ai_fix": None,
                            })
                    elif "[deep-security.log]" in line and semgrep_step_id:
                        content_match = re.match(r"^\[deep-security\.log\]\s*(?:\[[^\]]*\]\s*)?(.*)", line)
                        if not content_match:
                            continue
                        content = content_match.group(1)
                        m = re_semgrep.match(content)
                        if m:
                            parsed_findings.append({
                                "scan_type": "semgrep", "step_id": semgrep_step_id,
                                "severity": _normalize_severity(m.group(2)), "rule_id": m.group(3),
                                "file_path": m.group(4).strip(), "line_number": int(m.group(5)),
                                "message": m.group(6).strip()[:2000], "ai_fix": None,
                            })
                        else:
                            aifix_m = re_aifix.match(content)
                            if aifix_m and parsed_findings:
                                parsed_findings[-1]["ai_fix"] = aifix_m.group(1).strip()

                # GitHub에서 code snippet + DB 저장
                repo_url = obj.get("repo_url", "")
                branch = obj.get("branch", "main")
                snippet_cache: dict[str, list[str] | None] = {}

                for f in parsed_findings:
                    if f["scan_type"] == "gitleaks":
                        sev_counts["high"] += 1
                        gitleaks_count += 1
                    else:
                        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
                        semgrep_count += 1

                    ai_fix = f["ai_fix"]
                    if not ai_fix:
                        existing = await session.execute(
                            select(SecurityFinding.ai_fix_suggestion)
                            .where(
                                SecurityFinding.rule_id == f["rule_id"],
                                SecurityFinding.file_path == f["file_path"],
                                SecurityFinding.line_number == f["line_number"],
                                SecurityFinding.ai_fix_suggestion.isnot(None),
                            )
                            .limit(1)
                        )
                        row = existing.first()
                        if row and row[0]:
                            ai_fix = row[0]

                    # GitHub에서 code snippet
                    code_snippet = None
                    file_path = f["file_path"]
                    line_num = f["line_number"]
                    if file_path not in snippet_cache:
                        snippet_cache[file_path] = _fetch_file_from_github(repo_url, branch, file_path)
                    file_lines = snippet_cache[file_path]
                    if file_lines and line_num > 0:
                        ctx = 3
                        s = max(0, line_num - 1 - ctx)
                        e = min(len(file_lines), line_num + ctx)
                        snippet_lines = file_lines[s:e]
                        if f["scan_type"] == "gitleaks":
                            tidx = line_num - 1 - s
                            if 0 <= tidx < len(snippet_lines):
                                snippet_lines[tidx] = re.sub(
                                    r'["\']([^"\']{4,})["\']',
                                    lambda mx: '"****"',
                                    snippet_lines[tidx]
                                )
                        code_snippet = "\n".join(snippet_lines)

                    session.add(SecurityFinding(
                        job_id=job_id, step_id=f["step_id"],
                        scan_type=f["scan_type"], severity=f["severity"],
                        rule_id=f["rule_id"], rule_name=f["rule_id"],
                        file_path=f["file_path"], line_number=f["line_number"],
                        message=f["message"], is_masked=f["scan_type"] == "gitleaks",
                        ai_fix_suggestion=ai_fix, code_snippet=code_snippet,
                    ))

            total_findings = gitleaks_count + semgrep_count

            # ── 3) build_artifacts: build step에서 아티팩트 추출 ──
            build_step_id = step_map.get("build")
            if build_step_id:
                artifacts = _parse_build_artifacts(steps_data, obj.get("project_type", ""))
                for a in artifacts:
                    session.add(BuildArtifact(
                        job_id=job_id,
                        step_id=build_step_id,
                        artifact_name=a["name"],
                        artifact_type=a["type"],
                        location=a["location"],
                        size_bytes=a.get("size_bytes", 0),
                    ))

            # ── 4) security_summary: UPSERT — 엔진 verdict 스냅샷 우선 사용 ──
            if total_findings > 0 or step_map.get("lightweight-security") or step_map.get("deep-security"):
                # 엔진 verdict 블록 (새 스키마)
                verdict_block = security_block.get("verdict", {})
                eng_verdict = verdict_block.get("verdict") if isinstance(verdict_block, dict) else None
                eng_score = verdict_block.get("score") if isinstance(verdict_block, dict) else None
                eng_score_label = verdict_block.get("score_label") if isinstance(verdict_block, dict) else None
                eng_gauge_color = verdict_block.get("gauge_color") if isinstance(verdict_block, dict) else None
                eng_selected_items = verdict_block.get("selected_items", []) if isinstance(verdict_block, dict) else []
                eng_selected_count = verdict_block.get("selected_count") if isinstance(verdict_block, dict) else None
                eng_out_of_scope = verdict_block.get("out_of_scope_count", 0) if isinstance(verdict_block, dict) else 0
                eng_requires_approval = verdict_block.get("requires_approval", False) if isinstance(verdict_block, dict) else False
                eng_block_reasons = verdict_block.get("block_reasons", []) if isinstance(verdict_block, dict) else []
                eng_warn_reasons = verdict_block.get("warn_reasons", []) if isinstance(verdict_block, dict) else []
                eng_score_breakdown = verdict_block.get("score_breakdown", {}) if isinstance(verdict_block, dict) else {}
                eng_scanned_commit_sha = verdict_block.get("scanned_commit_sha") if isinstance(verdict_block, dict) else None
                eng_acknowledged_cwes = verdict_block.get("acknowledged_cwes", []) if isinstance(verdict_block, dict) else []

                # overall_status: 엔진 verdict 우선, fallback은 기존 로직
                _VERDICT_TO_STATUS = {
                    "pass": "passed", "block": "failed", "warn": "warning",
                    "block_pending_approval": "warning",
                }
                if eng_verdict:
                    overall = _VERDICT_TO_STATUS.get(eng_verdict, eng_verdict)
                    status_reason = "; ".join(eng_block_reasons + eng_warn_reasons) if (eng_block_reasons or eng_warn_reasons) else eng_verdict
                else:
                    overall = "passed"
                    reason_parts = []
                    if sev_counts["critical"] > 0:
                        overall = "failed"
                        reason_parts.append(f"critical={sev_counts['critical']}")
                    elif sev_counts["high"] > 0:
                        overall = "warning"
                        reason_parts.append(f"high={sev_counts['high']}")
                    elif sev_counts["medium"] > 0:
                        overall = "warning"
                        reason_parts.append(f"medium={sev_counts['medium']}")
                    if sev_counts["low"] > 0:
                        reason_parts.append(f"low={sev_counts['low']}")
                    status_reason = "; ".join(reason_parts) if reason_parts else "no findings"

                import json as _json
                from sqlalchemy import text as sa_text
                await session.execute(sa_text("""
                    INSERT INTO security_summary
                        (summary_id, job_id, total_findings, critical_count, high_count, medium_count, low_count,
                         gitleaks_count, semgrep_count, overall_status, status_reason,
                         verdict, score, score_label, gauge_color, selected_items, selected_count,
                         out_of_scope_count, requires_approval, block_reasons, warn_reasons, score_breakdown,
                         scanned_commit_sha, acknowledged_cwes)
                    VALUES
                        (gen_random_uuid(), :job_id, :total, :critical, :high, :medium, :low,
                         :gitleaks, :semgrep, :overall, :reason,
                         :verdict, :score, :score_label, :gauge_color, cast(:selected_items as jsonb), :selected_count,
                         :out_of_scope, :requires_approval, cast(:block_reasons as jsonb), cast(:warn_reasons as jsonb), cast(:score_breakdown as jsonb),
                         :scanned_commit_sha, cast(:acknowledged_cwes as jsonb))
                    ON CONFLICT (job_id) DO UPDATE SET
                        total_findings = :total, critical_count = :critical, high_count = :high,
                        medium_count = :medium, low_count = :low, gitleaks_count = :gitleaks,
                        semgrep_count = :semgrep, overall_status = :overall, status_reason = :reason,
                        verdict = :verdict, score = :score, score_label = :score_label,
                        gauge_color = :gauge_color, selected_items = cast(:selected_items as jsonb),
                        selected_count = :selected_count, out_of_scope_count = :out_of_scope,
                        requires_approval = :requires_approval, block_reasons = cast(:block_reasons as jsonb),
                        warn_reasons = cast(:warn_reasons as jsonb), score_breakdown = cast(:score_breakdown as jsonb),
                        scanned_commit_sha = :scanned_commit_sha, acknowledged_cwes = cast(:acknowledged_cwes as jsonb),
                        calculated_at = CURRENT_TIMESTAMP
                """), {
                    "job_id": job_id,
                    "total": total_findings,
                    "critical": sev_counts["critical"],
                    "high": sev_counts["high"],
                    "medium": sev_counts["medium"],
                    "low": sev_counts["low"],
                    "gitleaks": gitleaks_count,
                    "semgrep": semgrep_count,
                    "overall": overall,
                    "reason": status_reason,
                    "verdict": eng_verdict or overall,
                    "score": eng_score,
                    "score_label": eng_score_label,
                    "gauge_color": eng_gauge_color,
                    "selected_items": _json.dumps(eng_selected_items),
                    "selected_count": eng_selected_count,
                    "out_of_scope": eng_out_of_scope,
                    "requires_approval": eng_requires_approval,
                    "block_reasons": _json.dumps(eng_block_reasons),
                    "warn_reasons": _json.dumps(eng_warn_reasons),
                    "score_breakdown": _json.dumps(eng_score_breakdown),
                    "scanned_commit_sha": eng_scanned_commit_sha,
                    "acknowledged_cwes": _json.dumps(eng_acknowledged_cwes),
                })

            # ── 5) deployments: deploy step에서 배포 정보 추출 ──
            deploy_step_id = step_map.get("deploy")
            if deploy_step_id:
                deploy_info = _parse_deploy_info(steps_data)
                if deploy_info:
                    # flush 먼저 해서 build_artifacts가 DB에 들어가게 함
                    await session.flush()
                    art_result = await session.execute(
                        select(BuildArtifact.artifact_id)
                        .where(BuildArtifact.job_id == job_id)
                        .limit(1)
                    )
                    art_row = art_result.first()
                    if art_row:
                        session.add(Deployment(
                            job_id=job_id,
                            artifact_id=art_row.artifact_id,
                            target_env="production",
                            deployment_status=deploy_info["status"],
                            deployed_by=deploy_info.get("deployed_by", "ci-pipeline"),
                            deployed_at=_parse_time_safe(deploy_info.get("finished_at")),
                            deployment_result=deploy_info.get("metadata", {}),
                        ))

            await session.commit()
            print(f"[DB] Parsed data saved: {job_id} | findings={total_findings} gitleaks={gitleaks_count} semgrep={semgrep_count}")

    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[DB] _save_parsed_data_to_db failed: {exc}")


def _fetch_file_from_github(repo_url: str, branch: str, file_path: str) -> list[str] | None:
    """GitHub raw URL에서 소스 파일을 읽어 줄 단위 리스트로 반환."""
    # https://github.com/owner/repo.git → owner/repo
    import re as _re
    m = _re.search(r"github\.com[/:]([^/]+/[^/.]+)", repo_url)
    if not m:
        return None
    owner_repo = m.group(1)
    raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{file_path}"
    try:
        import urllib.request
        req = urllib.request.Request(raw_url, headers={"User-Agent": "ci-cd-backend"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        return content.splitlines()
    except Exception:
        return None


def _trim_snippet(raw_snippet: str | None, line_number: int, snippet_start_line: int, context: int = 2) -> tuple[str | None, int | None]:
    """엔진이 보낸 긴 snippet을 취약점 줄 기준 앞뒤 context줄로 잘라서 (snippet, start_line) 반환."""
    if not raw_snippet or line_number <= 0:
        return raw_snippet, snippet_start_line if raw_snippet else None
    lines = raw_snippet.splitlines()
    target_idx = line_number - snippet_start_line
    if target_idx < 0 or target_idx >= len(lines):
        return raw_snippet, snippet_start_line
    start = max(0, target_idx - context)
    end = min(len(lines), target_idx + context + 1)
    trimmed_start_line = snippet_start_line + start
    return "\n".join(lines[start:end]), trimmed_start_line


def _normalize_severity(raw: str) -> str:
    """다양한 severity 표현을 DB enum 값으로 정규화."""
    s = raw.strip().lower()
    if s in ("critical",):
        return "critical"
    if s in ("high", "error"):
        return "high"
    if s in ("medium", "warning"):
        return "medium"
    if s in ("low", "info"):
        return "low"
    return "medium"


def _parse_deploy_info(steps_data: list[dict]) -> dict | None:
    """deploy step에서 배포 정보 추출."""
    for step in steps_data:
        sname = step.get("step_name") or step.get("name", "")
        if sname != "deploy":
            continue
        status_raw = step.get("status", "")
        if status_raw == "skipped":
            return None
        dep_status = "success" if status_raw == "success" else "failed"
        summary = step.get("summary_message") or step.get("summary", "")

        metadata = {"summary": summary}
        # "Deployed owner/repo (runtime) to EC2 | hash=xxx" 파싱
        deploy_match = re.search(r"Deployed\s+(\S+)\s+\((\w+)\)\s+to\s+(\S+)", summary)
        if deploy_match:
            metadata["app"] = deploy_match.group(1)
            metadata["runtime"] = deploy_match.group(2)
            metadata["target"] = deploy_match.group(3)
        hash_match = re.search(r"hash=(\w+)", summary)
        if hash_match:
            metadata["deploy_hash"] = hash_match.group(1)

        return {
            "status": dep_status,
            "deployed_by": "ci-pipeline",
            "finished_at": step.get("finished_at"),
            "metadata": metadata,
        }
    return None


async def _save_log_lines(session, job_id: str, logs: list[str], step_map: dict[str, str]) -> None:
    """로그 라인을 step별로 모아서, step 하나당 row 하나로 저장."""
    # step별로 로그 라인 모으기
    step_logs_map: dict[str, list[str]] = {}
    step_level_map: dict[str, str] = {}  # step별 최고 로그 레벨 추적

    for line in logs:
        match = re.match(r"^\[([^.\]]+)\.log\]\s*(?:\[[^\]]*\]\s*)?(.*)", line)
        if not match:
            continue

        step_name = match.group(1)
        content = match.group(2).strip()

        if not content:
            continue
        # 내부 메타 라인 스킵
        if content.startswith("[step_status]") or content.startswith("[step_summary]") or \
           content.startswith("[step_exit_code]") or content.startswith("[exit_code]"):
            continue

        step_logs_map.setdefault(step_name, []).append(content)

        # 로그 레벨: error > warn > info (가장 높은 레벨을 step 전체에 적용)
        content_lower = content.lower()
        if "[error]" in content_lower or "error:" in content_lower or "exception" in content_lower:
            step_level_map[step_name] = "error"
        elif "[warn]" in content_lower or "warning" in content_lower:
            if step_level_map.get(step_name) != "error":
                step_level_map[step_name] = "warn"

    # step별로 합쳐서 하나의 row로 INSERT
    for step_name, lines in step_logs_map.items():
        step_id = step_map.get(step_name)
        if not step_id:
            continue
        combined = "\n".join(lines)
        log_level = step_level_map.get(step_name, "info")

        session.add(StepLog(
            job_id=job_id,
            step_id=step_id,
            log_level=log_level,
            log_content=combined,
        ))


def _parse_build_artifacts(steps_data: list[dict], project_type: str = "") -> list[dict]:
    """build step의 summary_message에서 아티팩트 정보 추출 (Java/Node.js/Python 모두 지원)."""
    artifacts = []
    seen = set()

    def _add(name: str, atype: str, location: str) -> None:
        if name and name not in seen:
            seen.add(name)
            artifacts.append({"name": name, "type": atype, "location": location, "size_bytes": 0})

    for step in steps_data:
        sname = step.get("step_name") or step.get("name", "")
        if sname != "build":
            continue
        summary = step.get("summary_message") or step.get("summary", "")

        # "artifacts saved: dist-server, build_meta.json"
        saved_match = re.search(r"artifacts?\s+saved:\s*(.+)", summary, re.IGNORECASE)
        if saved_match:
            art_names = [a.strip() for a in saved_match.group(1).split(",")]
            for name in art_names:
                if not name:
                    continue
                _add(name, _guess_artifact_type(name), f"build/{name}")

        # "artifacts: sample-java1-0.0.1-SNAPSHOT.jar"
        art_match = re.search(r"artifacts?:\s*(.+)", summary, re.IGNORECASE)
        if art_match and not saved_match:
            art_names = [a.strip() for a in art_match.group(1).split(",")]
            for name in art_names:
                if not name:
                    continue
                _add(name, _guess_artifact_type(name), f"build/libs/{name}" if name.endswith((".jar", ".war")) else f"build/{name}")

        # metadata에 artifacts 목록이 있으면
        meta = step.get("metadata", {})
        for art_name in meta.get("artifacts", []):
            if isinstance(art_name, str):
                _add(art_name, _guess_artifact_type(art_name), f"build/{art_name}")

    return artifacts


def _guess_artifact_type(name: str) -> str:
    """파일 이름으로 아티팩트 타입 추론."""
    n = name.lower()
    if n.endswith(".jar"):
        return "jar"
    if n.endswith(".war"):
        return "jar"
    if n.endswith(".whl"):
        return "wheel"
    if n.endswith(".tar.gz"):
        return "tar.gz"
    if n.endswith(".tar"):
        return "tar"
    if n.endswith(".zip"):
        return "zip"
    if n.endswith(".docker") or "docker" in n:
        return "docker"
    return "other"


# ── 보안 정책 카탈로그 (16개 항목) ───────────────────────────────────────────

SECURITY_CATALOG = [
    {"key": "sql-injection",           "name": "SQL Injection",                  "cwe": "CWE-89",   "grade": "critical"},
    {"key": "command-injection",       "name": "Command Injection",              "cwe": "CWE-78",   "grade": "critical"},
    {"key": "hardcoded-secret",        "name": "Hardcoded Secret",               "cwe": "CWE-798",  "grade": "critical"},
    {"key": "code-injection",          "name": "Code Injection",                 "cwe": "CWE-94",   "grade": "critical"},
    {"key": "insecure-deserialization","name": "Insecure Deserialization",        "cwe": "CWE-502",  "grade": "high"},
    {"key": "idor",                    "name": "IDOR",                           "cwe": "CWE-639",  "grade": "high"},
    {"key": "improper-jwt",            "name": "Improper JWT Verification",       "cwe": "CWE-347",  "grade": "high"},
    {"key": "cleartext-transmission",  "name": "Cleartext Transmission",         "cwe": "CWE-319",  "grade": "high"},
    {"key": "path-traversal",          "name": "Path Traversal",                 "cwe": "CWE-22",   "grade": "medium"},
    {"key": "xss",                     "name": "Cross-Site Scripting (XSS)",     "cwe": "CWE-79",   "grade": "medium"},
    {"key": "weak-crypto",             "name": "Weak Cryptography",              "cwe": "CWE-327",  "grade": "medium"},
    {"key": "ssrf",                    "name": "SSRF",                           "cwe": "CWE-918",  "grade": "medium"},
    {"key": "error-info-exposure",     "name": "Error Message Info Exposure",    "cwe": "CWE-209",  "grade": "low"},
    {"key": "missing-httponly",        "name": "Missing HttpOnly Flag",          "cwe": "CWE-1004", "grade": "low"},
    {"key": "missing-secure-flag",     "name": "Missing Secure Flag",            "cwe": "CWE-614",  "grade": "low"},
    {"key": "weak-password-policy",    "name": "Weak Password Requirements",     "cwe": "CWE-521",  "grade": "low"},
]

_CATALOG_BY_KEY = {item["key"]: item for item in SECURITY_CATALOG}
_CATALOG_BY_CWE = {item["cwe"]: item for item in SECURITY_CATALOG}


@app.get("/api/security/catalog")
def get_security_catalog() -> dict:
    """보안 정책 16개 항목 카탈로그."""
    by_grade: dict[str, list] = {"critical": [], "high": [], "medium": [], "low": []}
    for item in SECURITY_CATALOG:
        by_grade[item["grade"]].append(item)
    return {"total": len(SECURITY_CATALOG), "items": SECURITY_CATALOG, "by_grade": by_grade}


# ── 승인 워크플로 API ─────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/approval")
async def get_approval(job_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """해당 job의 승인 레코드 조회."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(ApprovalRecord).where(ApprovalRecord.job_id == job_id).order_by(ApprovalRecord.created_at.desc()).limit(1)
        )
        rec = result.scalar()
        if not rec:
            raise HTTPException(status_code=404, detail="승인 레코드 없음")
        return _approval_to_dict(rec)


@app.post("/api/jobs/{job_id}/approval/request")
async def request_approval(job_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """block_pending_approval 상태인 job에 대해 승인 레코드 생성."""
    async with SessionLocal() as session:
        job = await session.get(PipelineJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")

        summary_result = await session.execute(
            select(SecuritySummary).where(SecuritySummary.job_id == job_id)
        )
        summary = summary_result.scalar()
        if not summary or summary.verdict != "block_pending_approval":
            raise HTTPException(status_code=409, detail="이 job은 block_pending_approval 상태가 아닙니다")

        existing = await session.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.job_id == job_id,
                ApprovalRecord.status == "pending",
            )
        )
        if existing.scalar():
            raise HTTPException(status_code=409, detail="이미 승인 대기 중인 레코드가 있습니다")

        rec = ApprovalRecord(
            job_id=job_id,
            commit_sha=job.commit_sha,
            repo=job.repo_url,
            branch=job.branch,
            target_cwes=[r.split("(")[1].split(",")[0].strip() if "(" in r else "" for r in (summary.block_reasons or [])],
            block_reasons=summary.block_reasons or [],
            verdict_snapshot=summary.verdict_snapshot if hasattr(summary, "verdict_snapshot") else {},
            status="pending",
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        return _approval_to_dict(rec)


@app.post("/api/jobs/{job_id}/approval/approve")
async def approve_job(
    job_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """승인: reason 필수. block은 승인 불가.
    body: {"reason": "...", "approved_cwes": ["CWE-639"]}
    approved_cwes 미지정 시 block_reasons의 모든 CWE 수용(전체 승인).
    부분 승인: approved_cwes에 수용할 CWE만 명시. Critical CWE는 자동 제외."""
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="승인 사유(reason)를 입력하세요")

    async with SessionLocal() as session:
        summary_result = await session.execute(
            select(SecuritySummary).where(SecuritySummary.job_id == job_id)
        )
        summary = summary_result.scalar()
        if summary and summary.verdict == "block":
            raise HTTPException(status_code=403, detail="Critical 차단(block)은 승인 경로가 없습니다")

        result = await session.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.job_id == job_id,
                ApprovalRecord.status == "pending",
            ).order_by(ApprovalRecord.created_at.desc()).limit(1)
        )
        rec = result.scalar()
        if not rec:
            raise HTTPException(status_code=404, detail="승인 대기 레코드 없음")

        # 원본 잡 조회
        orig_job = await session.get(PipelineJob, job_id)
        if not orig_job:
            raise HTTPException(status_code=404, detail="원본 job not found")

        # approved_cwes: body에 명시된 CWE 우선, 없으면 block_reasons 전체 추출
        import re as _re
        if body.get("approved_cwes"):
            # 명시적 부분 승인: Critical은 제외
            critical_cwes = {"CWE-89", "CWE-78", "CWE-798", "CWE-94"}
            approved_cwes = [
                c for c in body["approved_cwes"]
                if c not in critical_cwes
            ]
        else:
            # 미명시 시 block_reasons의 모든 CWE 수용 (전체 승인)
            approved_cwes = list({
                m.group(0)
                for r in (rec.block_reasons or [])
                for m in [_re.search(r"CWE-\d+", r)]
                if m
            })

        now = datetime.now(timezone.utc)
        rec.status = "approved"
        rec.reason = reason
        rec.approver_id = str(current_user.get("github_login") or current_user.get("id", "unknown"))
        rec.approved_at = now
        rec.expires_at = body.get("expires_at") and _parse_time_safe(body["expires_at"])

        # 후속 잡 enqueue: 같은 repo+branch+commit_sha, approved_cwes 포함
        followup_id = str(uuid4())
        followup_job = PipelineJob(
            job_id=followup_id,
            repo_url=orig_job.repo_url,
            branch=orig_job.branch,
            trigger_source="approval",
            status="queued",
            source=orig_job.source,
            environment=orig_job.environment,
            workflow_path=orig_job.workflow_path,
            selected_items=orig_job.selected_items or [],
            commit_sha=orig_job.commit_sha,
            approved_cwes=approved_cwes,
            approval_record_id=rec.id,
            created_at=now,
        )
        session.add(followup_job)
        rec.followup_job_id = followup_id

        await session.commit()
        await session.refresh(rec)

        print(f"[approval] APPROVED job={job_id} by={rec.approver_id} reason={reason} → followup={followup_id} approved_cwes={approved_cwes}")
        result = _approval_to_dict(rec)
        result["followup_job_id"] = followup_id
        return result


@app.post("/api/jobs/{job_id}/approval/reject")
async def reject_job(
    job_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """거부."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.job_id == job_id,
                ApprovalRecord.status == "pending",
            ).order_by(ApprovalRecord.created_at.desc()).limit(1)
        )
        rec = result.scalar()
        if not rec:
            raise HTTPException(status_code=404, detail="승인 대기 레코드 없음")

        rec.status = "rejected"
        rec.reason = (body.get("reason") or "").strip() or None
        rec.approver_id = str(current_user.get("github_login") or current_user.get("id", "unknown"))
        rec.approved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(rec)

        print(f"[approval] REJECTED job={job_id} by={rec.approver_id}")
        return _approval_to_dict(rec)


@app.get("/api/approvals")
async def list_approvals(
    status: str | None = Query(None, description="pending|approved|rejected"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """승인 레코드 목록 (감사 로그용)."""
    async with SessionLocal() as session:
        q = select(ApprovalRecord).order_by(ApprovalRecord.created_at.desc())
        if status:
            q = q.where(ApprovalRecord.status == status)
        result = await session.execute(q.limit(100))
        records = result.scalars().all()
        return {"total": len(records), "records": [_approval_to_dict(r) for r in records]}


def _approval_to_dict(rec: ApprovalRecord) -> dict:
    return {
        "id": rec.id,
        "job_id": rec.job_id,
        "commit_sha": rec.commit_sha,
        "scanned_commit_sha": rec.scanned_commit_sha,
        "repo": rec.repo,
        "branch": rec.branch,
        "target_cwes": rec.target_cwes,
        "block_reasons": rec.block_reasons,
        "acknowledged_cwes": rec.acknowledged_cwes or [],
        "followup_job_id": rec.followup_job_id,
        "reason": rec.reason,
        "approver_id": rec.approver_id,
        "status": rec.status,
        "approved_at": rec.approved_at.isoformat() if rec.approved_at else None,
        "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


# ── 엔진 polling API ──────────────────────────────────────────────────────────

def _verify_engine_token(request: Request) -> None:
    """x-engine-token 헤더 검증."""
    settings = get_settings()
    expected = settings.engine_shared_token
    if not expected:
        raise HTTPException(status_code=503, detail="ENGINE_SHARED_TOKEN not configured")
    token = request.headers.get("x-engine-token", "")
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid engine token")


async def _job_to_engine_dict(job: PipelineJob, session) -> dict:
    from app.auth.crypto import decrypt_token
    from app.auth.token_store import get_token

    repo_token = None
    if job.user_id:
        # 메모리 캐시 우선
        user_result = await session.execute(
            select(User).where(User.id == job.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user:
            cached = get_token(user.github_id)
            if cached:
                repo_token = cached
            elif user.github_access_token_encrypted:
                try:
                    repo_token = decrypt_token(user.github_access_token_encrypted)
                except Exception:
                    repo_token = None

    return {
        "job_id": job.job_id,
        "repo_url": job.repo_url,
        "branch": job.branch,
        "source": job.source,
        "environment": job.environment,
        "workflow_path": job.workflow_path,
        "selected_items": job.selected_items or [],
        "commit_sha": job.commit_sha,
        "approved_cwes": job.approved_cwes or [],
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "repo_token": repo_token,
    }


@app.get("/api/jobs/pending")
async def get_pending_jobs(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """엔진이 polling해서 가져갈 pending 잡 목록. x-engine-token 인증 필요."""
    _verify_engine_token(request)
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(PipelineJob)
                .where(PipelineJob.status == "queued")
                .order_by(PipelineJob.created_at)
                .limit(limit)
            )
            jobs = result.scalars().all()
            return {"jobs": [await _job_to_engine_dict(j, session) for j in jobs]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/claim")
async def claim_job(job_id: str, request: Request) -> dict:
    """엔진이 잡을 가져갈 때 race condition 방지용 atomic claim.
    x-engine-token 인증 필요. queued → running 전환."""
    _verify_engine_token(request)
    engine_id = request.headers.get("x-engine-id", "unknown")
    try:
        async with SessionLocal() as session:
            job = await session.get(PipelineJob, job_id)
            if not job:
                raise HTTPException(status_code=404, detail="job not found")
            if job.status != "queued":
                raise HTTPException(status_code=409, detail=f"job is already {job.status}")

            now = datetime.now(timezone.utc)
            await session.execute(
                update(PipelineJob)
                .where(PipelineJob.job_id == job_id, PipelineJob.status == "queued")
                .values(status="running", started_at=now, claimed_at=now, claimed_by=engine_id)
            )
            await session.commit()
            await session.refresh(job)
            print(f"[engine] job claimed: {job_id} by {engine_id}")
            return await _job_to_engine_dict(job, session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── /api/jobs/{job_id} — 프론트엔드용 상세 조회 API ──────────────────────────

@app.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str) -> dict:
    """job 상세 조회: job 정보 + steps + security_summary(verdict) 포함."""
    try:
        async with SessionLocal() as session:
            # 1) pipeline_jobs
            job = await session.get(PipelineJob, job_id)
            if not job:
                raise HTTPException(status_code=404, detail="job not found")

            job_dict = {
                "job_id": job.job_id,
                "repo_url": job.repo_url,
                "branch": job.branch,
                "trigger_source": job.trigger_source,
                "status": job.status,
                "overall_result": job.overall_result,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_secs": job.duration_secs,
                "metadata": job.metadata_,
            }

            # 2) pipeline_steps (duration_secs 계산 포함)
            steps_result = await session.execute(
                select(PipelineStep)
                .where(PipelineStep.job_id == job_id)
                .order_by(PipelineStep.created_at)
            )
            steps_list = []
            for step in steps_result.scalars().all():
                duration = step.duration_secs
                if duration is None and step.started_at and step.ended_at:
                    duration = round((step.ended_at - step.started_at).total_seconds(), 2)
                steps_list.append({
                    "step_id": step.step_id,
                    "step_name": step.step_name,
                    "step_type": step.step_type,
                    "status": step.status,
                    "error_message": step.error_message,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "ended_at": step.ended_at.isoformat() if step.ended_at else None,
                    "duration_secs": duration,
                })

            # 현재 진행 중인 step
            current_step = None
            for s in steps_list:
                if s["status"] == "running":
                    current_step = s["step_name"]
                    break

            # 3) security_summary (verdict 역할)
            summary_result = await session.execute(
                select(SecuritySummary).where(SecuritySummary.job_id == job_id)
            )
            summary = summary_result.scalar()
            security_data = None
            if summary:
                security_data = {
                    "verdict": {
                        "overall_status": summary.overall_status,
                        "status_reason": summary.status_reason,
                        "total_findings": summary.total_findings,
                    },
                    "summaries": [
                        {"scanner": "gitleaks", "count": summary.gitleaks_count,
                         "critical": 0, "high": summary.gitleaks_count, "medium": 0, "low": 0},
                        {"scanner": "semgrep", "count": summary.semgrep_count,
                         "critical": summary.critical_count, "high": max(0, summary.high_count - summary.gitleaks_count),
                         "medium": summary.medium_count, "low": summary.low_count},
                    ],
                }

            return {
                "job": job_dict,
                "steps": steps_list,
                "current_step": current_step,
                "security": security_data,
            }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/findings")
async def get_job_findings(job_id: str) -> dict:
    """job의 security_findings 목록 조회."""
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(SecurityFinding)
                .where(SecurityFinding.job_id == job_id)
                .order_by(SecurityFinding.created_at)
            )
            findings = []
            for f in result.scalars().all():
                # code_snippet_start_line: 취약점 줄에서 앞 2줄 뺀 위치 (최소 1)
                snippet_start = None
                if f.code_snippet and f.line_number:
                    snippet_line_count = f.code_snippet.count("\n") + 1
                    lines_before = (snippet_line_count - 1) // 2  # 취약점 줄 앞에 몇 줄 있는지
                    snippet_start = max(1, f.line_number - lines_before)

                findings.append({
                    "finding_id": f.finding_id,
                    "scan_type": f.scan_type,
                    "severity": f.severity,
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "message": f.message,
                    "code_snippet": f.code_snippet,
                    "code_snippet_start_line": snippet_start,
                    "ai_fix_suggestion": f.ai_fix_suggestion,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                })
            return {"job_id": job_id, "count": len(findings), "findings": findings}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/pipeline-logs")
def pipeline_logs(job_id: str = Query(..., min_length=1)) -> dict:
    lines = result_fetcher.fetch_log_lines(job_id)
    return {"job_id": job_id, "lines": lines}


@app.get("/pipeline-steps")
def pipeline_steps(job_id: str = Query(..., min_length=1)) -> dict:
    steps = job_steps.get(job_id, [])
    return {"job_id": job_id, "steps": steps}


# ── /api/pipelines — 프론트엔드 API 스펙 ─────────────────────────────────────

@app.post("/api/pipelines", status_code=status.HTTP_202_ACCEPTED)
async def create_pipeline(
    req: StartPipelineRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """파이프라인 시작 (JWT 인증 필요). 202 Accepted 반환."""
    repo_url = str(req.repo_url)

    # 409: 동일 repo+branch가 이미 running/queued이면 중복 실행 방지
    try:
        async with SessionLocal() as session:
            conflict = await session.execute(
                select(PipelineJob.job_id)
                .where(
                    PipelineJob.repo_url == repo_url,
                    PipelineJob.branch == req.branch,
                    PipelineJob.status.in_(["queued", "running"]),
                )
                .limit(1)
            )
            row = conflict.first()
            if row:
                existing_job_id = str(row[0])
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"{repo_url} ({req.branch}) 브랜치에 이미 실행 중인 파이프라인이 있습니다",
                        "existing_job_id": existing_job_id,
                    },
                )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[DB] conflict check failed: {exc}")

    job_id = str(uuid4())
    now = datetime.now(timezone.utc)

    job_state[job_id] = {
        "status": "pending",
        "repo_url": repo_url,
        "branch": req.branch,
        "requested_at": now.isoformat(),
    }

    try:
        async with SessionLocal() as session:
            job = PipelineJob(
                job_id=job_id,
                repo_url=repo_url,
                branch=req.branch,
                trigger_source=req.trigger_source,
                status="queued",
                source=req.source,
                environment=req.environment,
                workflow_path=req.workflow_path,
                selected_items=req.selected_items or [],
                commit_sha=req.commit_sha,
                created_at=now,
                user_id=current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None),
            )
            session.add(job)
            await session.commit()
            print(f"[DB] pipeline_jobs INSERT (pending): {job_id}")
    except Exception as exc:
        print(f"[DB] pipeline_jobs INSERT failed: {exc}")

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "파이프라인이 큐에 등록되었습니다. 엔진이 곧 가져갑니다.",
    }


@app.post("/api/pipelines/{job_id}/cancel")
async def cancel_pipeline(
    job_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """실행 중인 파이프라인 취소. 우분투 프로세스를 kill하고 DB status를 cancelled로 변경."""
    async with SessionLocal() as session:
        job = await session.get(PipelineJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status not in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"job is already {job.status}, cannot cancel")

        # 우분투 프로세스 kill (실패해도 DB는 cancelled로 업데이트)
        killed = result_fetcher.kill_job(job_id)

        now = datetime.now(timezone.utc)
        await session.execute(
            update(PipelineJob)
            .where(PipelineJob.job_id == job_id)
            .values(status="cancelled", completed_at=now)
        )
        await session.commit()

    job_state.pop(job_id, None)
    job_steps.pop(job_id, None)

    return {
        "job_id": job_id,
        "status": "cancelled",
        "killed": killed,
        "message": "파이프라인이 취소되었습니다",
    }


@app.delete("/api/pipelines/{job_id}")
async def delete_pipeline(
    job_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """파이프라인 job 삭제. 실행 중이면 먼저 kill 후 DB에서 제거."""
    async with SessionLocal() as session:
        job = await session.get(PipelineJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")

        # 실행 중이면 우분투 프로세스도 kill
        if job.status in ("queued", "running"):
            result_fetcher.kill_job(job_id)

        # 연관 데이터 cascade 삭제 (DB에 ON DELETE CASCADE 없으면 수동 삭제)
        from sqlalchemy import text as sa_text
        await session.execute(sa_text("DELETE FROM step_logs WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM security_findings WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM security_summary WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM deployments WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM build_artifacts WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM pipeline_steps WHERE job_id = :jid"), {"jid": job_id})
        await session.execute(sa_text("DELETE FROM pipeline_jobs WHERE job_id = :jid"), {"jid": job_id})
        await session.commit()

    job_state.pop(job_id, None)
    job_steps.pop(job_id, None)

    # 로컬 result 파일도 삭제
    result_store.delete(job_id)

    return {"job_id": job_id, "deleted": True, "message": "파이프라인이 삭제되었습니다"}


@app.get("/api/pipelines/{job_id}/logs")
async def get_pipeline_logs(job_id: str) -> dict:
    """파이프라인 로그 조회 (path parameter)."""
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(PipelineStep.step_name, StepLog.log_content, StepLog.log_level)
                .join(StepLog, StepLog.step_id == PipelineStep.step_id)
                .where(StepLog.job_id == job_id)
                .order_by(PipelineStep.created_at)
            )
            rows = result.all()

        if rows:
            lines = []
            for step_name, log_content, _ in rows:
                if log_content:
                    for line in log_content.splitlines():
                        lines.append(f"[{step_name}.log] {line}")
            return {"job_id": job_id, "lines": lines}
    except Exception as exc:
        print(f"[pipeline logs] DB query failed: {exc}")

    # fallback: 메모리/파일에서 조회
    lines = result_fetcher.fetch_log_lines(job_id)
    return {"job_id": job_id, "lines": lines}


@app.get("/api/pipelines/{job_id}/steps")
async def get_pipeline_steps(job_id: str) -> dict:
    """파이프라인 step 목록 조회 (path parameter). job summary 필드 포함."""
    try:
        async with SessionLocal() as session:
            job = await session.get(PipelineJob, job_id)

            result = await session.execute(
                select(PipelineStep)
                .where(PipelineStep.job_id == job_id)
                .order_by(PipelineStep.created_at)
            )
            steps_list = []
            for step in result.scalars().all():
                duration = step.duration_secs
                if duration is None and step.started_at and step.ended_at:
                    duration = round((step.ended_at - step.started_at).total_seconds(), 2)
                steps_list.append({
                    "step_id": step.step_id,
                    "step_name": step.step_name,
                    "step_type": step.step_type,
                    "status": step.status,
                    "error_message": step.error_message,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "ended_at": step.ended_at.isoformat() if step.ended_at else None,
                    "duration_secs": duration,
                })

            job_summary = None
            if job:
                job_summary = {
                    "status": job.status,
                    "repo_url": job.repo_url,
                    "branch": job.branch,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "duration_secs": job.duration_secs,
                }

        if steps_list or job_summary:
            return {"job_id": job_id, "job": job_summary, "steps": steps_list}
    except Exception as exc:
        print(f"[pipeline steps] DB query failed: {exc}")

    # fallback: 메모리에서 조회
    steps = job_steps.get(job_id, [])
    return {"job_id": job_id, "job": None, "steps": steps}


@app.get("/api/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    severity: str | None = Query(None, description="comma-separated: critical,high,medium,low"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """보안 분석 상세 결과 (findings 리스트 + AI 제안 포함)."""
    try:
        async with SessionLocal() as session:
            job = await session.get(PipelineJob, job_id)
            if not job:
                raise HTTPException(status_code=404, detail="job not found")

            # 진행 중인 job은 빈 findings 반환
            if job.status in ("queued", "running"):
                return {
                    "job_id": job_id,
                    "repo_url": job.repo_url,
                    "branch": job.branch,
                    "completed_at": None,
                    "scores": {"security_score": 0, "code_quality_score": 0},
                    "verdict": {"overall_status": "pending", "status_reason": "파이프라인 실행 중", "total_findings": 0},
                    "severity_summary": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "scanner_summaries": [],
                    "findings": [],
                    "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False},
                }

            # security_summary
            summary_result = await session.execute(
                select(SecuritySummary).where(SecuritySummary.job_id == job_id)
            )
            summary = summary_result.scalar()

            # findings 쿼리
            sev_filter = []
            if severity:
                sev_filter = [s.strip() for s in severity.split(",") if s.strip()]

            findings_query = select(SecurityFinding).where(SecurityFinding.job_id == job_id)
            if sev_filter:
                findings_query = findings_query.where(SecurityFinding.severity.in_(sev_filter))
            findings_query = findings_query.order_by(
                SecurityFinding.severity,
                SecurityFinding.created_at,
            )

            total_result = await session.execute(
                select(func.count()).select_from(
                    findings_query.subquery()
                )
            )
            total = total_result.scalar() or 0

            findings_result = await session.execute(
                findings_query.offset(offset).limit(limit)
            )
            findings = findings_result.scalars().all()

            # ── 전체 findings(페이지네이션 무관) 기준으로 집계 ──
            all_findings_result = await session.execute(
                select(SecurityFinding.scan_type, SecurityFinding.severity)
                .where(SecurityFinding.job_id == job_id)
            )
            all_rows = all_findings_result.all()

            all_sev_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            scanner_map: dict[str, dict] = {}
            for scan_type, sev in all_rows:
                all_sev_counts[sev] = all_sev_counts.get(sev, 0) + 1
                if scan_type not in scanner_map:
                    scanner_map[scan_type] = {"scanner": scan_type, "count": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
                scanner_map[scan_type]["count"] += 1
                scanner_map[scan_type][sev] = scanner_map[scan_type].get(sev, 0) + 1

            # severity_summary: DB summary 우선, 없으면 findings 직접 집계
            eff_critical = summary.critical_count if summary else all_sev_counts["critical"]
            eff_high     = summary.high_count     if summary else all_sev_counts["high"]
            eff_medium   = summary.medium_count   if summary else all_sev_counts["medium"]
            eff_low      = summary.low_count      if summary else all_sev_counts["low"]

            # security_score: 엔진 score 우선, 없으면 findings 기준 계산
            if summary and summary.score is not None:
                sec_score = summary.score
            else:
                penalty = eff_critical * 20 + eff_high * 10 + eff_medium * 3 + eff_low * 1
                sec_score = max(0, 100 - penalty)

            # code_quality_score: critical/high findings 수에 따라 감점
            cq_penalty = eff_critical * 15 + eff_high * 5 + eff_medium * 2 + eff_low * 1
            code_quality_score = max(0, 100 - cq_penalty)

            findings_list = []
            for f in findings:
                snippet_start = None
                if f.code_snippet and f.line_number:
                    snippet_line_count = f.code_snippet.count("\n") + 1
                    lines_before = (snippet_line_count - 1) // 2
                    snippet_start = max(1, f.line_number - lines_before)

                findings_list.append({
                    "id": f.finding_id,
                    "scanner": f.scan_type,
                    "rule_id": f.rule_id,
                    "cwe": f.cwe_id,
                    "policy_item": f.policy_item,
                    "in_scope": f.in_scope,
                    "cve": None,
                    "cvss": str(f.cvss_score) if f.cvss_score else None,
                    "cvss_version": None,
                    "title": f.rule_name or f.rule_id,
                    "severity": f.severity,
                    "file_path": f.file_path,
                    "line_start": f.line_number,
                    "line_end": f.line_number,
                    "code_snippet": f.code_snippet,
                    "code_snippet_start_line": snippet_start,
                    "description": f.message,
                    "ai_suggestion": f.ai_fix_suggestion,
                    "references": [],
                })

            # approval 레코드 조회
            approval_result = await session.execute(
                select(ApprovalRecord)
                .where(ApprovalRecord.job_id == job_id)
                .order_by(ApprovalRecord.created_at.desc())
                .limit(1)
            )
            approval = approval_result.scalar()

            return {
                "job_id": job_id,
                "repo_url": job.repo_url,
                "branch": job.branch,
                "commit_sha": job.commit_sha,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "scores": {
                    "security_score": sec_score,
                    "score_label": summary.score_label if summary else None,
                    "gauge_color": summary.gauge_color if summary else "green",
                    "code_quality_score": code_quality_score,
                },
                "verdict": {
                    "verdict": summary.verdict if summary else "pass",
                    "overall_status": summary.overall_status if summary else "passed",
                    "status_reason": summary.status_reason if summary else "no findings",
                    "total_findings": summary.total_findings if summary else 0,
                    "requires_approval": summary.requires_approval if summary else False,
                    "block_reasons": summary.block_reasons if summary else [],
                    "warn_reasons": summary.warn_reasons if summary else [],
                    # job 생성 시 프론트가 보낸 key 배열 우선, 엔진 verdict의 selected_items는 형식이 다를 수 있음
                    "selected_items": job.selected_items or [],
                    "selected_count": summary.selected_count if summary else len(job.selected_items or []),
                    "out_of_scope_count": summary.out_of_scope_count if summary else 0,
                    "score_breakdown": summary.score_breakdown if summary else {},
                },
                "severity_summary": {
                    "critical": eff_critical,
                    "high": eff_high,
                    "medium": eff_medium,
                    "low": eff_low,
                },
                "scanner_summaries": list(scanner_map.values()),
                "findings": findings_list,
                "approval": _approval_to_dict(approval) if approval else None,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < total,
                },
            }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/get-results", response_model=PipelineResultResponse)
def get_result(job_id: str = Query(..., min_length=1)) -> PipelineResultResponse:
    data = result_store.load(job_id)
    if data is None:
        remote_data = result_fetcher.fetch_result_by_job_id(job_id=job_id)
        if remote_data is not None:
            result_store.save(job_id, remote_data)
            existing = job_state.get(job_id, {})
            existing.update(
                {
                    "status": remote_data.get("status", existing.get("status", "unknown")),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            job_state[job_id] = existing
            return PipelineResultResponse(found=True, data=PipelineResultPayload(**remote_data), message="ok")

        return PipelineResultResponse(found=False, data=None, message="result not found yet")

    return PipelineResultResponse(found=True, data=PipelineResultPayload(**data), message="ok")


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page() -> HTMLResponse:
    """최근 job 목록과 콜백/DB 데이터를 한눈에 볼 수 있는 모니터링 페이지."""
    try:
        async with SessionLocal() as session:
            jobs_result = await session.execute(
                select(PipelineJob)
                .order_by(PipelineJob.created_at.desc())
                .limit(20)
            )
            jobs = jobs_result.scalars().all()

            rows = []
            for job in jobs:
                steps_result = await session.execute(
                    select(PipelineStep)
                    .where(PipelineStep.job_id == job.job_id)
                    .order_by(PipelineStep.started_at)
                )
                steps = steps_result.scalars().all()

                summary_result = await session.execute(
                    select(SecuritySummary).where(SecuritySummary.job_id == job.job_id)
                )
                summary = summary_result.scalar()

                findings_count = await session.execute(
                    select(func.count()).where(SecurityFinding.job_id == job.job_id)
                )
                f_count = findings_count.scalar()

                rows.append({
                    "job": job,
                    "steps": steps,
                    "summary": summary,
                    "findings_count": f_count,
                })

    except Exception as exc:
        return HTMLResponse(f"<pre>DB 오류: {exc}</pre>", status_code=500)

    from datetime import timedelta
    KST = timezone(timedelta(hours=9))

    def status_badge(s):
        color = {"success": "#22c55e", "failed": "#ef4444", "running": "#f59e0b",
                 "queued": "#6b7280", "skipped": "#94a3b8"}.get(s, "#6b7280")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;font-size:12px">{s}</span>'

    def fmt_time(dt):
        if not dt:
            return "-"
        return dt.astimezone(KST).strftime("%m-%d %H:%M:%S")

    job_rows_html = ""
    for r in rows:
        job = r["job"]
        summary = r["summary"]
        steps = r["steps"]
        f_count = r["findings_count"]

        step_pills = ""
        for s in steps:
            color = {"success": "#22c55e", "failed": "#ef4444", "skipped": "#94a3b8", "running": "#f59e0b"}.get(s.status, "#6b7280")
            step_pills += f'<span style="background:{color};color:#fff;padding:1px 6px;border-radius:8px;font-size:11px;margin:1px">{s.step_name}</span> '

        if summary:
            sec_html = f"""
            <div style="font-size:12px;margin-top:4px">
                {status_badge(summary.overall_status)}
                <span style="margin-left:6px">총 {summary.total_findings}건
                | 🔴 {summary.critical_count}
                | 🟠 {summary.high_count}
                | 🟡 {summary.medium_count}
                | 🟢 {summary.low_count}</span>
            </div>"""
        else:
            sec_html = '<div style="font-size:12px;color:#94a3b8;margin-top:4px">보안 스캔 없음</div>'

        job_rows_html += f"""
        <tr>
            <td style="padding:10px;font-size:12px;font-family:monospace;color:#94a3b8">{str(job.job_id)[:8]}…</td>
            <td style="padding:10px;font-size:12px">{job.repo_url.split('github.com/')[-1] if job.repo_url else '-'}<br>
                <span style="color:#94a3b8">{job.branch}</span></td>
            <td style="padding:10px">{status_badge(job.status)}</td>
            <td style="padding:10px;font-size:12px">{fmt_time(job.created_at)}</td>
            <td style="padding:10px;font-size:12px">{fmt_time(job.completed_at)}</td>
            <td style="padding:10px;font-size:12px">{int(job.duration_secs) if job.duration_secs else '-'}s</td>
            <td style="padding:10px">{step_pills or '<span style="color:#94a3b8;font-size:12px">없음</span>'}</td>
            <td style="padding:10px">
                <span style="font-size:13px;font-weight:600">{f_count}건</span>
                {sec_html}
            </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="10">
<title>CI/CD 모니터</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .subtitle {{ color: #64748b; font-size: 13px; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; }}
  th {{ background: #334155; padding: 10px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
  tr:nth-child(even) {{ background: #172033; }}
  tr:hover {{ background: #263348; }}
  .refresh {{ float: right; font-size: 12px; color: #64748b; }}
</style>
</head>
<body>
<h1>CI/CD 파이프라인 모니터</h1>
<div class="subtitle">최근 20개 job · 10초마다 자동 갱신 <span class="refresh">갱신: {datetime.now(KST).strftime('%H:%M:%S')} KST</span></div>
<table>
  <thead>
    <tr>
      <th>Job ID</th>
      <th>Repo / Branch</th>
      <th>Status</th>
      <th>시작</th>
      <th>완료</th>
      <th>소요</th>
      <th>Steps</th>
      <th>보안 결과 (Findings)</th>
    </tr>
  </thead>
  <tbody>
    {job_rows_html if job_rows_html else '<tr><td colspan="8" style="padding:20px;text-align:center;color:#64748b">아직 job이 없습니다</td></tr>'}
  </tbody>
</table>
</body>
</html>"""
    return HTMLResponse(html)
