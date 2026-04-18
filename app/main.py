import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import func, select, update

from app.api.repos_router import router as repos_router
from app.auth.router import router as auth_router
from app.db import Base, SessionLocal, engine
from app.db_models import (  # noqa: F401
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
async def start_pipeline(req: StartPipelineRequest) -> StartPipelineResponse:
    job_id = str(uuid4())
    now = datetime.now(timezone.utc)

    job_state[job_id] = {
        "status": "queued",
        "repo_url": str(req.repo_url),
        "branch": req.branch,
        "requested_at": now.isoformat(),
    }

    # DB에 pipeline_jobs INSERT
    try:
        async with SessionLocal() as session:
            job = PipelineJob(
                job_id=job_id,
                repo_url=str(req.repo_url),
                branch=req.branch,
                trigger_source=req.trigger_source,
                status="queued",
                created_at=now,
            )
            session.add(job)
            await session.commit()
            print(f"[DB] pipeline_jobs INSERT: {job_id}")
    except Exception as exc:
        print(f"[DB] pipeline_jobs INSERT failed: {exc}")

    try:
        trigger_service.trigger(job_id=job_id, repo_url=str(req.repo_url), branch=req.branch)
        job_state[job_id]["status"] = "triggered"
        # DB status 업데이트
        try:
            async with SessionLocal() as session:
                await session.execute(
                    update(PipelineJob).where(PipelineJob.job_id == job_id).values(status="running", started_at=datetime.now(timezone.utc))
                )
                await session.commit()
        except Exception:
            pass
    except Exception as exc:
        job_state[job_id]["status"] = "failed_to_trigger"
        job_state[job_id]["error"] = str(exc)
        # DB status 업데이트 (failed)
        try:
            async with SessionLocal() as session:
                await session.execute(
                    update(PipelineJob).where(PipelineJob.job_id == job_id).values(status="failed")
                )
                await session.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"failed to trigger ubuntu pipeline: {exc}") from exc

    return StartPipelineResponse(
        job_id=job_id,
        status="triggered",
        message="pipeline was triggered on ubuntu",
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
                job = PipelineJob(
                    job_id=payload.job_id,
                    repo_url=payload.repo_url,
                    branch=payload.branch,
                    trigger_source="callback",
                    status="running",
                    started_at=payload.started_at or datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
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
            started = payload.started_at
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

            # ── 2) security_findings: 로그에서 gitleaks/semgrep 파싱 ──
            gitleaks_count = 0
            semgrep_count = 0
            sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

            gitleaks_step_id = step_map.get("lightweight-security")
            semgrep_step_id = step_map.get("deep-security")

            # gitleaks 로그 패턴: [N] rule=RULE | FILE:LINE | MESSAGE
            re_gitleaks = re.compile(
                r"\s*\[(\d+)\]\s+rule=([^\s|]+)\s*\|\s*([^:]+):(\d+)\s*\|\s*(.*)"
            )
            # semgrep 로그 패턴: [N] [SEVERITY] RULE_ID | FILE:LINE | MESSAGE
            re_semgrep = re.compile(
                r"\s*\[(\d+)\]\s+\[(\w+)\]\s+([^\s|]+)\s*\|\s*([^:]+):(\d+)\s*\|\s*(.*)"
            )

            for line in logs:
                # gitleaks findings
                if "[lightweight-security.log]" in line and gitleaks_step_id:
                    # 로그 내용 추출 (접두사 제거)
                    content_match = re.match(r"^\[lightweight-security\.log\]\s*(?:\[[^\]]*\]\s*)?(.*)", line)
                    if not content_match:
                        continue
                    content = content_match.group(1)
                    m = re_gitleaks.match(content)
                    if m:
                        gitleaks_count += 1
                        sev_counts["high"] += 1  # gitleaks는 기본 high
                        session.add(SecurityFinding(
                            job_id=job_id,
                            step_id=gitleaks_step_id,
                            scan_type="gitleaks",
                            severity="high",
                            rule_id=m.group(2),
                            rule_name=m.group(2),
                            file_path=m.group(3).strip(),
                            line_number=int(m.group(4)),
                            message=m.group(5).strip(),
                            is_masked=True,
                        ))

                # semgrep findings
                elif "[deep-security.log]" in line and semgrep_step_id:
                    content_match = re.match(r"^\[deep-security\.log\]\s*(?:\[[^\]]*\]\s*)?(.*)", line)
                    if not content_match:
                        continue
                    content = content_match.group(1)
                    m = re_semgrep.match(content)
                    if m:
                        severity = _normalize_severity(m.group(2))
                        semgrep_count += 1
                        sev_counts[severity] = sev_counts.get(severity, 0) + 1
                        session.add(SecurityFinding(
                            job_id=job_id,
                            step_id=semgrep_step_id,
                            scan_type="semgrep",
                            severity=severity,
                            rule_id=m.group(3),
                            rule_name=m.group(3),
                            file_path=m.group(4).strip(),
                            line_number=int(m.group(5)),
                            message=m.group(6).strip()[:2000],
                            is_masked=False,
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

            # ── 4) security_summary: UPSERT (트리거가 먼저 빈 row를 만들 수 있음) ──
            if total_findings > 0 or step_map.get("lightweight-security") or step_map.get("deep-security"):
                overall = "passed"
                reason_parts = []
                if sev_counts["critical"] > 0:
                    overall = "failed"
                    reason_parts.append(f"critical={sev_counts['critical']}")
                if sev_counts["high"] > 0:
                    overall = "failed"
                    reason_parts.append(f"high={sev_counts['high']}")
                if sev_counts["medium"] > 0:
                    if overall == "passed":
                        overall = "warning"
                    reason_parts.append(f"medium={sev_counts['medium']}")
                if sev_counts["low"] > 0:
                    reason_parts.append(f"low={sev_counts['low']}")
                if not reason_parts:
                    reason_parts.append("no findings")

                from sqlalchemy import text as sa_text
                await session.execute(sa_text("""
                    INSERT INTO security_summary
                        (summary_id, job_id, total_findings, critical_count, high_count, medium_count, low_count,
                         gitleaks_count, semgrep_count, overall_status, status_reason)
                    VALUES
                        (gen_random_uuid(), :job_id, :total, :critical, :high, :medium, :low,
                         :gitleaks, :semgrep, :overall, :reason)
                    ON CONFLICT (job_id) DO UPDATE SET
                        total_findings = :total, critical_count = :critical, high_count = :high,
                        medium_count = :medium, low_count = :low, gitleaks_count = :gitleaks,
                        semgrep_count = :semgrep, overall_status = :overall, status_reason = :reason,
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
                    "reason": "; ".join(reason_parts),
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


@app.get("/pipeline-logs")
def pipeline_logs(job_id: str = Query(..., min_length=1)) -> dict:
    lines = result_fetcher.fetch_log_lines(job_id)
    return {"job_id": job_id, "lines": lines}


@app.get("/pipeline-steps")
def pipeline_steps(job_id: str = Query(..., min_length=1)) -> dict:
    steps = job_steps.get(job_id, [])
    return {"job_id": job_id, "steps": steps}


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
