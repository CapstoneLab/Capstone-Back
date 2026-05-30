# 엔진 ↔ 백엔드 연동 가이드

> 백엔드 베이스 URL: `http://54.221.222.244/capstonelab/capstone-back`
> 최종 업데이트: 2026-05-30 (v3 — commit_sha 검증 + approved_cwes 승인 재트리거 합의 반영)

---

## 목차

1. [전체 흐름 개요](#1-전체-흐름-개요)
2. [인증](#2-인증)
3. [잡 Polling — 잡 가져오기](#3-잡-polling--잡-가져오기)
4. [잡 Claim — 실행 시작 선언](#4-잡-claim--실행-시작-선언)
5. [잡 Payload 상세 — selected_items / environment / approved_cwes](#5-잡-payload-상세--selected_items--environment--approved_cwes)
6. [보안 정책 카탈로그 16개 항목](#6-보안-정책-카탈로그-16개-항목)
7. [콜백 — step_complete](#7-콜백--step_complete)
8. [콜백 — pipeline_complete](#8-콜백--pipeline_complete)
9. [security.verdict 스키마 상세](#9-securityverdict-스키마-상세)
10. [security.findings 스키마 상세](#10-securityfindings-스키마-상세)
11. [verdict별 백엔드 동작](#11-verdict별-백엔드-동작)
12. [승인 후 재배포 흐름 (block_pending_approval)](#12-승인-후-재배포-흐름-block_pending_approval)
12. [에러 응답 형식](#12-에러-응답-형식)
13. [엔드포인트 요약표](#13-엔드포인트-요약표)

---

## 1. 전체 흐름 개요

```
[프론트엔드]
    │  POST /api/pipelines  (JWT 인증)
    │  selected_items, environment, commit_sha 포함
    ▼
[백엔드]
    │  pipeline_jobs INSERT (status=queued)
    │  즉시 202 응답 → 프론트엔드에 job_id 반환
    ▼
[엔진 Polling Daemon] ─── 15초 주기 ───────────────────────┐
    │  GET /api/jobs/pending  (x-engine-token 인증)         │
    │  → queued 잡 목록 수신                                │
    │                                                       │
    │  POST /api/jobs/{job_id}/claim                        │
    │  → status: queued → running 원자적 전환               │
    │                                                       │
    │  selected_items, environment, commit_sha 포함된       │
    │  payload로 main.py 실행                               │
    │                                                       │
    │  [스캔 진행 중]                                        │
    │  POST /get-results  (step_complete 콜백)  ────────────┘
    │  POST /get-results  (pipeline_complete 콜백)
    ▼
[백엔드]
    │  verdict 파싱 → DB 저장
    │  block_pending_approval이면 approval_records 대기
    ▼
[프론트엔드]
    GET /api/jobs/{job_id}/result  로 결과 polling
```

---

## 2. 인증

엔진이 백엔드 API를 호출할 때는 **모든 요청**에 아래 헤더를 포함해야 합니다.

```
x-engine-token: b5404290c67c2f89676bd48beeee6cfadcbcb8c7f4ac184c85c9d472db4d011a
x-engine-id: ubuntu-engine-01   ← 엔진 식별자 (자유롭게 설정, claim 로그에 기록됨)
```

| 응답 코드 | 의미 |
|-----------|------|
| `401` | 토큰 불일치 |
| `503` | 백엔드에 토큰 미설정 (서버 설정 오류) |

콜백 엔드포인트(`/get-results`)는 토큰 인증 없이 호출합니다.

---

## 3. 잡 Polling — 잡 가져오기

```
GET /api/jobs/pending?limit=5
x-engine-token: {token}
x-engine-id: {engine_id}
```

**Query Parameters**

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | int | 10 | 최대 반환 수 (1~50) |

**Response 200**

```json
{
  "jobs": [
    {
      "job_id": "e0ce2c15-f7f9-4157-9a25-c533258189db",
      "repo_url": "https://github.com/owner/repo",
      "branch": "main",
      "source": "capstone",
      "environment": "development",
      "workflow_path": null,
      "selected_items": ["CWE-89", "sql-injection", "idor"],
      "commit_sha": "a1b2c3d4e5f6...",
      "approved_cwes": [],
      "created_at": "2026-05-30T10:00:00.000000+00:00"
    }
  ]
}
```

- `jobs`가 빈 배열이면 현재 대기 중인 잡 없음 → 다음 주기에 재시도
- `selected_items`가 빈 배열이면 16개 전체 검사
- `approved_cwes`가 비어 있지 않으면 승인된 후속 잡 — 해당 CWE의 High finding은 게이트 통과 처리

---

## 4. 잡 Claim — 실행 시작 선언

```
POST /api/jobs/{job_id}/claim
x-engine-token: {token}
x-engine-id: {engine_id}
```

Body 없음.

**Response 200** — claim 성공, 동일한 job payload 반환

```json
{
  "job_id": "f1a2b3c4-...",
  "repo_url": "https://github.com/owner/repo",
  "branch": "main",
  "source": "capstone",
  "environment": "development",
  "workflow_path": null,
  "selected_items": ["CWE-89", "idor"],
  "commit_sha": "a1b2c3d4",
  "approved_cwes": ["CWE-639"],
  "created_at": "2026-05-30T10:10:00.000000+00:00"
}
```

**에러 응답**

| 코드 | detail | 처리 방법 |
|------|--------|-----------|
| `404` | job not found | 폴링 목록에서 제거 |
| `409` | job is already running | 다른 엔진이 선점함, 스킵 |

> **Race condition 방지**: 여러 엔진 인스턴스가 동시에 같은 잡을 가져가려 할 때 409가 발생합니다. 409는 정상 시나리오이므로 에러 로그 불필요.

---

## 5. 잡 Payload 상세 — selected_items / environment / approved_cwes

### selected_items

엔진 CLI 실행 시 `--selected-items` 인자로 전달합니다.

```bash
python main.py \
  --repo {repo_url} \
  --branch {branch} \
  --environment {environment} \
  --selected-items "{selected_items를 콤마로 join}" \
  --callback-url {callback_url} \
  --callback-token {token} \
  --job-id {job_id}
```

- `selected_items`가 빈 배열이면 `--selected-items` 인자 생략 → 16개 전체 검사
- CWE ID(`CWE-89`)와 key(`sql-injection`) 혼용 가능

**예시**
```bash
# selected_items = ["CWE-89", "idor", "xss"] 인 경우
python main.py \
  --repo https://github.com/owner/repo \
  --branch main \
  --environment production \
  --selected-items "CWE-89,idor,xss" \
  --callback-url http://54.221.222.244/capstonelab/capstone-back/get-results \
  --job-id e0ce2c15-f7f9-4157-9a25-c533258189db
```

### environment 의미

| environment | 동작 |
|-------------|------|
| `development`, `feature` | 매뉴얼 위계 그대로. Medium = WARN (통과) |
| `production`, `staging` | Medium도 `block_pending_approval`로 승격. 더 엄격 |

> `environment`는 게이트를 완화하는 방향으로는 절대 작동하지 않습니다.

### commit_sha 처리 (합의됨)

payload에 `commit_sha`가 있으면 해당 커밋을 정확히 checkout해서 스캔합니다.

```bash
# commit_sha가 있는 경우
git clone --depth 1 --branch {branch} {repo_url} .
git fetch origin {commit_sha}   # shallow clone 후 추가 fetch
git checkout {commit_sha}

# fetch 실패 시 (강제푸시/rebase로 sha 소실) → branch HEAD로 fallback
git clone --depth 1 --branch {branch} {repo_url} .
# 콜백에 scanned_commit_sha = 실제 HEAD SHA를 echo해서 불일치 기록
```

- `commit_sha`가 null이면 branch HEAD 스캔
- 콜백 `security.verdict`에 `scanned_commit_sha` 필드를 항상 포함할 것

### approved_cwes 처리 (합의됨)

`approved_cwes`가 비어 있지 않으면 승인된 재실행 잡입니다.

```
approved_cwes: ["CWE-639"]  → IDOR (High) finding을 "인지·수용됨"으로 처리 → 게이트 통과
```

**규칙:**
- `approved_cwes`에 있는 CWE에 해당하는 **High** finding → 게이트 차단 없이 통과
- **Critical finding은 approved_cwes와 무관하게 항상 `block`** — 절대 수용 불가
- 콜백 `security.verdict`에 `acknowledged_cwes` 필드를 echo할 것 (감사 저장)

---

## 6. 보안 정책 카탈로그 16개 항목

```
GET /api/security/catalog
```

인증 불필요. 항상 아래 16개 항목을 반환합니다.

| key | name | cwe | grade |
|-----|------|-----|-------|
| `sql-injection` | SQL Injection | CWE-89 | critical |
| `command-injection` | Command Injection | CWE-78 | critical |
| `hardcoded-secret` | Hardcoded Secret | CWE-798 | critical |
| `code-injection` | Code Injection | CWE-94 | critical |
| `insecure-deserialization` | Insecure Deserialization | CWE-502 | high |
| `idor` | IDOR | CWE-639 | high |
| `improper-jwt` | Improper JWT Verification | CWE-347 | high |
| `cleartext-transmission` | Cleartext Transmission | CWE-319 | high |
| `path-traversal` | Path Traversal | CWE-22 | medium |
| `xss` | Cross-Site Scripting (XSS) | CWE-79 | medium |
| `weak-crypto` | Weak Cryptography | CWE-327 | medium |
| `ssrf` | SSRF | CWE-918 | medium |
| `error-info-exposure` | Error Message Info Exposure | CWE-209 | low |
| `missing-httponly` | Missing HttpOnly Flag | CWE-1004 | low |
| `missing-secure-flag` | Missing Secure Flag | CWE-614 | low |
| `weak-password-policy` | Weak Password Requirements | CWE-521 | low |

엔진의 `app/security_catalog.py`가 단일 소스입니다. 백엔드는 이 표를 그대로 복제합니다.

---

## 7. 콜백 — step_complete

스텝 하나가 완료될 때마다 전송합니다.

```
POST /get-results
Content-Type: application/json
```

```json
{
  "job_id": "e0ce2c15-f7f9-4157-9a25-c533258189db",
  "type": "step_complete",
  "pipeline_status": "running",
  "repo_url": "https://github.com/owner/repo",
  "branch": "main",
  "step": {
    "name": "lightweight-security",
    "step_name": "lightweight-security",
    "step_type": "security",
    "status": "success",
    "started_at": "2026-05-30T10:01:00+00:00",
    "finished_at": "2026-05-30T10:01:45+00:00",
    "duration_secs": 45.2,
    "error_message": null,
    "metadata": {}
  }
}
```

**Response 200**
```json
{"message": "step recorded"}
```

---

## 8. 콜백 — pipeline_complete

파이프라인 전체 완료 시 1회 전송합니다.

```
POST /get-results
Content-Type: application/json
```

```json
{
  "job_id": "e0ce2c15-f7f9-4157-9a25-c533258189db",
  "type": "pipeline_complete",
  "status": "failed",
  "repo_url": "https://github.com/owner/repo",
  "branch": "main",
  "started_at": "2026-05-30T10:00:30+00:00",
  "ended_at": "2026-05-30T10:05:00+00:00",
  "logs": [
    "[lightweight-security.log] [INFO] gitleaks scan complete",
    "[deep-security.log] [WARN] semgrep found 2 issues"
  ],
  "steps": [...],
  "security": {
    "findings": [...],
    "verdict": {...}
  }
}
```

> `status`는 엔진 파이프라인 전체 성공/실패 여부입니다.
> `block`, `block_pending_approval` verdict일 때는 `"status": "failed"`로 전송합니다.
> verdict 값으로 둘을 구분합니다.

**Response 200**
```json
{"message": "result stored"}
```

---

## 9. security.verdict 스키마 상세

`pipeline_complete` 콜백의 `security.verdict` 객체입니다.

```json
{
  "verdict": "block_pending_approval",
  "score": 94.0,
  "score_label": "94.0/100 (검사 항목 3개 기준)",
  "gauge_color": "orange",
  "environment": "development",
  "counts": {
    "critical": 0,
    "high": 1,
    "medium": 1,
    "low": 0
  },
  "selected_count": 3,
  "selected_items": [
    {"key": "sql-injection", "name": "SQL Injection",               "cwe": "CWE-89",  "grade": "critical"},
    {"key": "idor",          "name": "IDOR",                        "cwe": "CWE-639", "grade": "high"},
    {"key": "xss",           "name": "Cross-Site Scripting (XSS)",  "cwe": "CWE-79",  "grade": "medium"}
  ],
  "out_of_scope_count": 0,
  "requires_approval": true,
  "score_breakdown": {
    "critical": 0.0,
    "high": 5.0,
    "medium": 1.0,
    "low": 0.0
  },
  "block_reasons": [
    "IDOR (CWE-639, High) 검출 — 승인 필요 정책"
  ],
  "warn_reasons": [],
  "scanned_commit_sha": "a1b2c3d4e5f6789...",
  "acknowledged_cwes": []
}
```

**verdict 값 4종**

| verdict | 의미 | status | requires_approval | gauge_color |
|---------|------|--------|-------------------|-------------|
| `pass` | Low만 있거나 없음 | `success` | false | `green` |
| `warn` | Medium ≥ 1 | `success` | false | `yellow` |
| `block_pending_approval` | High ≥ 1 (또는 strict env의 Medium) | `failed` | **true** | `orange` |
| `block` | Critical ≥ 1 | `failed` | false | `red` |

**필드 설명**

| 필드 | 타입 | 설명 |
|------|------|------|
| `verdict` | string | 4종 중 하나 |
| `score` | float | 0~100. 선택한 항목 기준으로 계산 |
| `score_label` | string | UI에 그대로 표시할 점수 문자열 |
| `gauge_color` | string | UI 게이지 색상. 백엔드가 점수로 재계산하지 않음 |
| `selected_items` | array | 이번 스캔에서 검사한 항목 목록 |
| `selected_count` | int | `selected_items` 길이 |
| `out_of_scope_count` | int | 선택 범위 밖에서 추가 탐지된 finding 수 |
| `requires_approval` | bool | true이면 백엔드가 승인 워크플로 진입 |
| `block_reasons` | array | 차단 사유 문자열 목록. UI에 그대로 표시 |
| `warn_reasons` | array | 경고 사유 문자열 목록 |
| `score_breakdown` | object | 등급별 감점 내역 `{critical, high, medium, low}` |
| `scanned_commit_sha` | string | 실제 스캔한 커밋 SHA. commit_sha fetch 실패 시 HEAD SHA |
| `acknowledged_cwes` | array | approved_cwes 중 실제 수용 처리된 CWE 목록. 감사용 |

---

## 10. security.findings 스키마 상세

`pipeline_complete` 콜백의 `security.findings[]` 각 항목입니다.

```json
{
  "rule_id": "semgrep-rule-id",
  "title": "IDOR via direct object reference",
  "scanner_name": "semgrep",
  "severity": "high",
  "cwe": "CWE-639",
  "policy_item": "idor",
  "in_scope": true,
  "file_path": "app/api/users.py",
  "line_number": 42,
  "column_number": 8,
  "code_snippet": "user = User.get(request.params['id'])",
  "code_snippet_start_line": 41,
  "message": "User object fetched without ownership check",
  "ai_recommendation": "요청한 사용자가 해당 리소스의 소유자인지 확인하세요.",
  "metadata": {}
}
```

**신규 필드 (기존 필드 유지, 추가됨)**

| 필드 | 타입 | 설명 |
|------|------|------|
| `cwe` | string | `"CWE-639"` 형식. 16개 카탈로그 CWE 또는 기타 |
| `policy_item` | string \| null | 매칭된 카탈로그 key (`"idor"`). 미매칭 시 `null` |
| `in_scope` | bool | 이번 `selected_items` 범위 내 여부. 범위 밖이면 `false` |

**scanner_name 값**

| 값 | 설명 |
|----|------|
| `"gitleaks"` | 시크릿/하드코딩 키 탐지 |
| `"semgrep"` | 코드 취약점 탐지 |

**severity 값**: `critical` / `high` / `medium` / `low`

---

## 11. verdict별 백엔드 동작

```
verdict = "pass"
  → pipeline_jobs.overall_result = "success"
  → 프론트에 녹색 게이지 표시

verdict = "warn"
  → pipeline_jobs.overall_result = "success"  (배포 진행)
  → 프론트에 경고 배지 + warn_reasons 표시

verdict = "block_pending_approval"
  → pipeline_jobs.overall_result = "failed"
  → security_summary.requires_approval = true
  → 프론트가 POST /api/jobs/{id}/approval/request 호출
  → 승인자가 POST /api/jobs/{id}/approval/approve 호출
  → 승인 후 해당 커밋 한정으로 배포 진행
  ※ block(Critical)과 달리 승인 경로 존재

verdict = "block"
  → pipeline_jobs.overall_result = "failed"
  → 승인 API 호출 시 403 반환 (하드 차단)
  → 코드 수정 후 재실행 필요
```

---

## 12. 에러 응답 형식

모든 에러는 아래 형식으로 반환됩니다.

```json
{
  "error": "UNAUTHORIZED",
  "detail": "Invalid engine token",
  "message": "Invalid engine token"
}
```

| HTTP 코드 | error 값 |
|-----------|----------|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` |
| 500 | `INTERNAL_SERVER_ERROR` |
| 503 | `SERVICE_UNAVAILABLE` |

---

## 13. 엔드포인트 요약표

### 엔진 전용 (x-engine-token 인증)

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/jobs/pending?limit=5` | queued 잡 목록 polling |
| `POST` | `/api/jobs/{job_id}/claim` | 잡 claim (queued → running) |

### 콜백 (인증 없음)

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/get-results` | step_complete / pipeline_complete 콜백 수신 |

### 정보 조회 (인증 없음)

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/security/catalog` | 보안 정책 16개 항목 카탈로그 |
| `GET` | `/health` | 서버 헬스체크 |

### 프론트엔드용 (JWT 인증)

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/pipelines` | 파이프라인 시작 (selected_items 포함) |
| `GET` | `/api/jobs/{job_id}` | job 상태 + steps + security 요약 |
| `GET` | `/api/jobs/{job_id}/result` | 보안 결과 상세 (findings + verdict 전체) |
| `GET` | `/api/jobs/{job_id}/findings` | finding 목록만 조회 |
| `POST` | `/api/jobs/{job_id}/approval/request` | 승인 요청 생성 |
| `POST` | `/api/jobs/{job_id}/approval/approve` | 승인 (body: `{"reason":"..."}`) |
| `POST` | `/api/jobs/{job_id}/approval/reject` | 거부 |
| `GET` | `/api/jobs/{job_id}/approval` | 해당 job 승인 레코드 조회 |
| `GET` | `/api/approvals?status=pending` | 전체 승인 감사 로그 |

---

---

## 12. 승인 후 재배포 흐름 (block_pending_approval)

### 전체 시퀀스

```
1. 엔진이 High finding 탐지
   → verdict = "block_pending_approval", requires_approval = true
   → pipeline_complete 콜백 전송 (status = "failed")

2. 백엔드가 security_summary에 verdict 저장
   → 프론트엔드가 GET /api/jobs/{id}/result 에서 verdict 확인

3. 담당자가 POST /api/jobs/{id}/approval/request
   → approval_records INSERT (status = pending)

4. 보안 책임자가 POST /api/jobs/{id}/approval/approve
   Body (전체 승인):  {"reason": "내부 검토 후 수용 결정 — 차기 스프린트 패치 예정"}
   Body (부분 승인):  {"reason": "IDOR만 수용, JWT는 이번 스프린트 내 패치", "approved_cwes": ["CWE-639"]}
   → 미명시 시 block_reasons의 모든 CWE 수용
   → Critical CWE는 approved_cwes에 넣어도 자동 제외
   → approval_records UPDATE (status = approved)
   → 백엔드가 approved_cwes 포함 후속 잡 자동 enqueue
   Response: {"followup_job_id": "f1a2b3c4-..."}

5. 엔진 poller가 후속 잡을 claim
   payload: {
     ...원본 잡과 동일,
     "approved_cwes": ["CWE-639"],   ← 수용된 CWE
     "commit_sha": "a1b2c3d4"        ← 동일 커밋
   }

6. 엔진이 같은 커밋 재스캔
   - CWE-639 (IDOR) High finding → 게이트 통과 처리
   - 나머지 finding은 정상 게이트 적용
   - deploy 스텝 진행

7. 엔진이 pipeline_complete 콜백 전송
   security.verdict: {
     "verdict": "warn" or "pass",
     "acknowledged_cwes": ["CWE-639"],   ← echo
     "scanned_commit_sha": "a1b2c3d4"    ← echo
   }

8. 백엔드가 followup 잡 결과 저장
   → acknowledged_cwes 감사 로그에 기록
```

### 커밋 한정 스코프

승인은 해당 `commit_sha`에만 유효합니다.

```
커밋 A  →  block_pending_approval  →  승인  →  재스캔 통과 ✅
커밋 B  →  동일 CWE High 검출  →  새 block_pending_approval (재승인 필요) ❌
```

새 커밋의 잡에는 `approved_cwes`가 포함되지 않으므로 자동으로 다시 차단됩니다.

### Critical은 승인 경로 없음

```
verdict = "block" (Critical ≥ 1)
  → POST /api/jobs/{id}/approval/approve 호출 시 403 반환
  → approved_cwes에 Critical CWE가 있어도 엔진이 무시하고 차단 유지
  → 코드 수정 후 새 커밋으로 재실행 필요
```

---

*이 가이드는 백엔드 코드와 동기화되어 있습니다. 스키마 변경 시 함께 업데이트됩니다.*
