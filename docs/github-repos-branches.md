# GitHub 레포/브랜치 불러오기 (Phase 2) 작업 매뉴얼

Phase 1 (GitHub OAuth 로그인) 위에 쌓은 두 번째 단계입니다. 로그인된 사용자의
access_token 으로 **레포 목록 → 브랜치 목록**을 GitHub API 에서 가져와서,
`run_login.cmd` 한 창 안에서 대화형으로 고를 수 있게 하는 것이 목표입니다.

Phase 2 는 DB 저장을 하지 않습니다. access_token 은 in-memory 캐시에 두고,
나중에 DB 가 붙으면 `repositories` 테이블로 옮기면 됩니다.

---

## 1. 전체 흐름

```
┌─────────────────── 한 개의 cmd 창 ───────────────────┐
│                                                      │
│  run_login.cmd                                       │
│    ├─ venv + pip                                     │
│    ├─ scripts\kill_orphans.ps1  (좀비 정리)          │
│    ├─ uvicorn 백그라운드 기동 → server.log           │
│    ├─ /health 폴링                                    │
│    ├─ 브라우저 자동 오픈 → /auth/github/login         │
│    │                                                  │
│    │   ┌──────────── 서버 (백그라운드) ────────────┐  │
│    │   │ /auth/github/callback                      │  │
│    │   │   - 토큰 교환                               │  │
│    │   │   - token_store.put_token(gid, token)      │  │
│    │   │   - %TEMP%\cicd_last_jwt.txt 에 JWT 기록   │  │
│    │   └──────────────────────────────────────────┘  │
│    │                                                  │
│    └─ select_repo.py (포그라운드, 인터랙티브)         │
│         ├─ JWT 파일 감지 (최대 180초 폴링)            │
│         ├─ GET /api/debug/whoami → scopes/orgs 출력  │
│         ├─ GET /api/repos → 레포 번호 선택            │
│         ├─ GET /api/repos/{owner}/{repo}/branches    │
│         │    → 브랜치 번호 선택 (b = 뒤로가기)        │
│         └─ 최종 선택 결과 출력 → "또 고를래?"         │
│                                                      │
│  CLI 종료 시 run_login.cmd 가 서버를 자동 종료        │
└──────────────────────────────────────────────────────┘
```

---

## 2. 변경/추가된 파일

### 백엔드

| 파일 | 역할 |
|------|------|
| `app/auth/token_store.py` | `github_id → access_token` 인메모리 dict. `put_token` / `get_token` / `clear_token` |
| `app/auth/router.py` | 콜백에서 `put_token(...)` 호출, JWT 를 `%TEMP%\cicd_last_jwt.txt` 에 드롭 |
| `app/api/__init__.py` | 빈 패키지 마커 |
| `app/api/github_client.py` | httpx 기반 GitHub API 헬퍼 — `list_user_repos`, `list_repo_branches`, `list_user_orgs`, `list_org_repos`, `fetch_token_scopes` |
| `app/api/repos_router.py` | `/api/repos`, `/api/repos/{owner}/{repo}/branches`, `/api/debug/whoami` |
| `app/main.py` | `repos_router` include |

### CLI / 런처

| 파일 | 역할 |
|------|------|
| `select_repo.py` | 인터랙티브 레포·브랜치 선택기 (JWT 파일 폴링 → 리스트 → 선택) |
| `run_login.cmd` | 한 창 안에서 서버 백그라운드 + 브라우저 + 셀렉터를 오케스트레이션 |
| `scripts\kill_orphans.ps1` | 포트 점유자 + 고아 uvicorn worker 를 한 번에 정리 |

---

## 3. 엔드포인트 요약

모두 `Authorization: Bearer <JWT>` 가 필요합니다. JWT 는 Phase 1 의 `/auth/github/callback` 이 발급합니다.

### `GET /api/repos`
`/user/repos?affiliation=owner,collaborator,organization_member` 를 페이지네이션으로 다 긁고, 추가로 `/user/orgs` → 각 org 별 `/orgs/{org}/repos` 까지 병합합니다. OAuth App 이 org 에 승인돼 있지 않으면 두 경로 모두 org 레포를 못 가져오므로 "org 가 안 뜨는 것처럼" 보입니다 (→ 8장).

응답:
```json
{
  "count": 14,
  "orgs_detected": [],
  "org_errors": [],
  "repos": [
    {
      "id": 123,
      "name": "Comtime-FE",
      "full_name": "printwd/Comtime-FE",
      "owner": "printwd",
      "private": true,
      "default_branch": "main",
      "language": "TypeScript",
      "html_url": "https://github.com/printwd/Comtime-FE",
      "updated_at": "2026-04-11T..."
    }
  ]
}
```

### `GET /api/repos/{owner}/{repo}/branches`
해당 레포의 브랜치 목록. 응답:
```json
{
  "owner": "printwd",
  "repo": "Comtime-FE",
  "count": 3,
  "branches": [
    { "name": "main", "protected": true, "commit_sha": "abc1234..." },
    { "name": "develop", "protected": false, "commit_sha": "..." }
  ]
}
```

### `GET /api/debug/whoami` (진단용)
```json
{
  "user":   { "github_id": 123, "github_login": "printwd", "source": "jwt" },
  "scopes": ["read:user", "repo", "user:email"],
  "orgs":   [{ "login": "MyOrg", "id": 999, "description": null }]
}
```
`scopes` 는 `/user` 호출 시 응답 헤더 `X-OAuth-Scopes` 를 파싱합니다. `orgs` 가 비어있으면 OAuth 앱이 org 에 승인 안 된 상태 (→ 8장).

---

## 4. access_token 저장 전략

- **in-memory only** (`app/auth/token_store.py`). 서버 재시작하면 사라집니다.
- JWT 는 사용자 식별(gid)만 담고 있고, 실제 GitHub API 호출용 access_token 은 따로 `token_store` 에서 꺼냅니다.
- 그래서 "서버 재시작 후 기존 JWT 로 `/api/repos` 호출" → `401 github access token not in cache; please log in again via /auth/github/login`. 정상 동작입니다. 그냥 다시 로그인하면 됩니다.
- 향후 DB 단계에서는 `users.github_access_token_encrypted` (이미 Fernet 컬럼 정의돼 있음) 를 소스 오브 트루스로 쓰고, 캐시는 hot path 가속용으로만 씁니다.

---

## 5. CLI ↔ 서버 연결 방식: JWT 임시 파일

CLI 가 로그인 JWT 를 자동으로 받기 위해 다음 규칙을 씁니다.

- 서버: `/auth/github/callback` 이 JWT 를 `%TEMP%\cicd_last_jwt.txt` 에 기록 (`app/auth/router.py:JWT_TEMP_FILE`).
- CLI: `select_repo.py` 가 시작할 때 기존 파일을 지우고, 새로 생길 때까지 1초 간격으로 180초 폴링합니다.
- `run_login.cmd` 도 시작할 때 이 파일을 지우므로, 이전 세션의 JWT 가 잘못 재사용될 일이 없습니다.

JWT 파일이 감지 안 되면 수동으로 붙여넣을 수 있게 프롬프트가 뜹니다.

---

## 6. `run_login.cmd` 단일 창 구조

핵심 포인트:

1. **좀비 청소** — `scripts\kill_orphans.ps1` 가 (a) 포트 리스너, (b) `multiprocessing.spawn|uvicorn.*app\.main` 패턴의 python 프로세스를 모두 강제 종료. (Phase 1 에서 고생했던 orphan worker 이슈 근본 해결)
2. **백그라운드 기동** — `start /B "" cmd /c ""%PY%" -m uvicorn ... > server.log 2>&1"`. 새 창 없음, 로그는 `server.log`.
3. **`--reload` 끔** — Windows 에서 `--reload` 는 reloader 부모 + worker 자식을 띄우는데, 백그라운드 모드에선 자식이 고아가 되기 쉬워 비활성. 코드 수정 시 실시간 리로드가 필요하면 일시적으로 `run_login.cmd` 에서 `--reload` 를 살려 별창 모드로 돌리는 걸 권장.
4. **readiness 폴링** — 30회까지 `/health` 에 curl 해서 진짜로 뜨는지 확인. 실패 시 `server.log` 끝부분 30줄을 화면에 찍고 pause.
5. **pause 로 마무리** — CLI 종료 후에도 창이 닫히지 않고 결과를 볼 수 있게 함.

---

## 7. `select_repo.py` 사용법

```
[select_repo] token scopes : ['read:user', 'repo', 'user:email']
[select_repo] orgs detected : ['MyOrg']

=== Repositories (15) ===
  1. [PRIVATE] printwd/Comtime-FE  ...
  ...

[select_repo] Pick a repository [1-15] (q=quit):
```

- 숫자 입력 → 해당 레포 선택
- `q` / `quit` → 종료
- 브랜치 선택 화면에서만 `b` / `back` → 레포 목록으로 복귀

최종 선택 후:
```
========================================
  repo   : printwd/Comtime-FE
  branch : develop
  url    : https://github.com/printwd/Comtime-FE
  sha    : abc1234...
========================================

[select_repo] Pick another? [y/N]:
```

---

## 8. Organization 레포가 안 보일 때 (가장 자주 겪는 이슈)

증상: `orgs detected : (none — OAuth app may not be approved at org level)` 이면서 레포 목록에 org 소유 레포가 하나도 없음.

원인: Organization 설정의 **Third-party application access policy** 가 켜져 있고, 이 OAuth App 이 아직 org 에 **승인되지 않음**. 이 경우 GitHub 는 `/user/repos`, `/user/orgs`, `/orgs/{org}/repos` 모두에서 해당 org 와 그 레포를 **완전히 숨깁니다** — `repo` scope 이 있어도 소용 없습니다.

해결:

1. 본인 GitHub 계정으로 접속:  
   `https://github.com/settings/connections/applications/<CLIENT_ID>`  
   (현재 `CLIENT_ID` 는 `.env` 의 `GITHUB_CLIENT_ID`)
2. 페이지 하단 **"Organization access"** 섹션 확인:
   - ✅ **Granted** → 정상. 이 상태라면 즉시 보여야 함 (안 보이면 토큰 재발급 필요 — 로그아웃 후 재로그인)
   - 🔵 **Grant** 버튼 → 본인이 org owner 인 경우, 누르면 즉시 접근 가능
   - 🟡 **Request** 버튼 → owner 에게 승인 요청 이메일 전송. owner 가 승인해줄 때까지 org 레포 안 보임
3. Grant/승인 완료 후:
   - `run_login.cmd` 창의 CLI 에서 `q` 로 나가기
   - `run_login.cmd` 재실행 → 다시 로그인 → 이번엔 org 레포가 병합돼서 뜸

### "Organization access 섹션 자체가 안 보인다"면
해당 org 가 OAuth App 을 아예 차단 중. Org owner 가 **Organization Settings → Third-party access → OAuth app policy** 에서 승인해줘야 합니다.

### 진단 명령어
```
curl -H "Authorization: Bearer <JWT>" http://127.0.0.1:8000/api/debug/whoami
```
`scopes`, `orgs`, `orgs_error` 필드를 보면 어느 단계에서 막혔는지 바로 확인됩니다.

---

## 9. 트러블슈팅 히스토리

### 9-1. "cmd 창이 바로 꺼진다"
**원인:** `run_login.cmd` 안에 인라인 powershell 명령을 `^` 라인 계속 문자와 이스케이프된 쌍따옴표로 섞어 놓으면 cmd 파서가 터집니다.  
**해결:** 해당 powershell 로직을 `scripts\kill_orphans.ps1` 로 분리했고, 모든 exit 지점에 `pause` 를 넣어 창이 닫히지 않게 했습니다.

### 9-2. "uvicorn 이 안 뜨고 구 버전 서버가 응답한다"
**원인:** 이전에 `--reload` 로 돌렸던 uvicorn 의 **multiprocessing worker 자식**이 부모가 죽은 뒤에도 살아남아 포트 8000 을 계속 물고 있음. 새 uvicorn 은 `[Errno 10048] bind` 로 실패하지만 `server.log` 를 안 보면 모르고, curl 은 구 서버가 응답하므로 정상처럼 보임.  
**진단:** `powershell Get-NetTCPConnection -LocalPort 8000 -State Listen` 으로 OwningProcess 조회. `Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'"` 로 `multiprocessing.spawn` 을 참조하는 python 프로세스 찾기.  
**해결:** `scripts\kill_orphans.ps1` 가 이걸 자동화합니다. 추가로 `run_login.cmd` 는 이제 `--reload` 를 아예 안 씁니다.

### 9-3. "`token scopes : (unknown)` 이라고 뜬다"
**원인:** `/api/debug/whoami` 호출이 실패했는데 CLI 가 에러를 삼켰던 케이스. 실제로는 구 버전 서버(whoami 엔드포인트 없음)가 응답 중이었고, 즉 9-2 의 증상.  
**해결:** CLI 에 `whoami error / scopes error / orgs error` 라인 추가. 그리고 9-2 해결로 구 버전 서버가 안 살아남게 됨.

### 9-4. "scopes 에 `repo` 가 없다"
**원인:** 사용자가 이 OAuth 앱을 **예전에 더 적은 scope 으로 authorize** 한 상태로 캐싱돼 있음. Phase 1/2 코드가 `repo` scope 을 요청해도 재동의 화면이 안 뜸.  
**해결:** `https://github.com/settings/applications` 에서 해당 앱 **Revoke** → `run_login.cmd` 재실행 → 새 동의 화면에서 `repo` 체크 확인.

---

## 10. 향후 작업 (Phase 3 후보)

1. **DB 연결** — Postgres 를 실제로 띄우고 `users`, `repositories`, `branches` 테이블을 채우기. `token_store` 는 DB 컬럼(`github_access_token_encrypted`, Fernet)에서 load 하는 캐시로 재구성.
2. **레포 선택 결과 → 파이프라인 트리거** — `select_repo.py` 최종 선택을 바로 `/start-pipeline` 으로 연결 (현재는 출력만).
3. **웹훅 연동** — 선택된 레포에 webhook 등록 → push 시 자동 파이프라인 트리거.
4. **브랜치 추가 정보** — 마지막 커밋 메시지, 작성자, 시간을 같이 가져와서 선택 화면에 표시.
5. **기존 폴링 방식 대체** — JWT 임시 파일 폴링은 투박함. 같은 머신이니 uvicorn 에 `POST /cli/hand-off` 같은 엔드포인트를 두고 callback 에서 내부 호출해 파일 I/O 없이 전달하는 방식으로 개선 가능.

---

## 부록 A. 파일 경로 총정리

```
app/
  api/
    __init__.py
    github_client.py          # httpx 래퍼
    repos_router.py           # /api/repos, /api/repos/{o}/{r}/branches, /api/debug/whoami
  auth/
    router.py                 # /auth/github/* + JWT 임시파일 드롭
    token_store.py            # in-memory github_id → access_token
  main.py                     # repos_router include

scripts/
  kill_orphans.ps1            # 포트/좀비 정리

select_repo.py                # 인터랙티브 CLI
run_login.cmd                 # 단일 창 런처
docs/
  github-oauth-login.md       # Phase 1 매뉴얼
  github-repos-branches.md    # 이 문서 (Phase 2)
```

## 부록 B. 수동 테스트 스니펫

```
# 1. 서버 뜬 상태에서 JWT 를 브라우저 로그인으로 얻은 뒤
set JWT=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# 2. 진단
curl -H "Authorization: Bearer %JWT%" http://127.0.0.1:8000/api/debug/whoami

# 3. 레포 목록 (org 병합 포함)
curl -H "Authorization: Bearer %JWT%" http://127.0.0.1:8000/api/repos

# 4. 특정 레포의 브랜치
curl -H "Authorization: Bearer %JWT%" http://127.0.0.1:8000/api/repos/printwd/Comtime-FE/branches
```
