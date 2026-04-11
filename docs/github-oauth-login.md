# GitHub 소셜 로그인 작업 매뉴얼

**작성일**: 2026-04-12
**브랜치**: `feat/github-auth`
**범위**: 1단계 — GitHub OAuth 로그인만. 레포/파이프라인/보안 스캔은 이후 단계.

---

## 1. 무엇을 만들었나

FastAPI 백엔드에 GitHub OAuth 2.0 인증을 붙였습니다. 사용자가 `/auth/github/login`을 누르면 github.com에서 로그인 → 승인 → 돌아와서 JWT를 발급받습니다. JWT는 Bearer 헤더로 다른 API 호출에 사용합니다.

### 플로우 개요

```
[브라우저] ──GET /auth/github/login──▶ [우리 서버]
                                           │
                                           ▼
[브라우저] ◀──302 github.com/login/oauth/authorize──┘
     │
     ▼ (GitHub에서 ID/비번 입력 + Authorize)
     │
[브라우저] ──GET /auth/github/callback?code=...&state=...──▶ [우리 서버]
                                                                │
                                                                ├─ state 쿠키 검증
                                                                ├─ code → access_token 교환
                                                                ├─ GitHub API로 프로필 조회
                                                                ├─ DB upsert (실패 시 스킵)
                                                                ├─ access_token 암호화 저장
                                                                ├─ JWT 발급
                                                                ▼
[브라우저] ◀──302 /auth/success?token=<JWT>──┘
     │
     ▼
[브라우저] ──GET /auth/me (Authorization: Bearer <JWT>)──▶ [우리 서버]
     │                                                       │
[브라우저] ◀──JSON 프로필──────────────────────────────────┘
```

---

## 2. 파일 구조

### 신규 파일
| 파일 | 역할 |
|------|------|
| `app/db.py` | SQLAlchemy 2.0 async 엔진, `Base`, `get_db` 의존성 |
| `app/db_models.py` | `User` ORM 모델 (users 테이블) |
| `app/auth/__init__.py` | 빈 패키지 마커 |
| `app/auth/crypto.py` | Fernet 대칭 암호화 (access_token 저장용) |
| `app/auth/jwt_utils.py` | JWT 인코딩/디코딩 + `get_current_user` 의존성 |
| `app/auth/github_oauth.py` | authorize URL 생성, code 교환, GitHub API 호출 |
| `app/auth/router.py` | `/auth/github/login`, `/auth/github/callback`, `/auth/me`, `/auth/success` 라우터 |
| `run_login.cmd` | 원클릭 실행 스크립트 |

### 수정된 파일
| 파일 | 변경 내용 |
|------|-----------|
| `app/config.py` | `DATABASE_URL`, `GITHUB_CLIENT_ID/SECRET`, `JWT_SECRET` 등 설정 추가 |
| `app/main.py` | lifespan에서 `create_all` + auth 라우터 include |
| `requirements.txt` | `sqlalchemy[asyncio]`, `asyncpg`, `PyJWT`, `cryptography` 추가 |
| `.env` | GitHub OAuth, JWT, DB 관련 변수 추가 |

---

## 3. DB 스키마 (users 테이블)

문서상의 스키마에서 2개 컬럼을 추가했습니다.

```sql
CREATE TABLE users (
    id                            BIGSERIAL PRIMARY KEY,
    github_id                     BIGINT       NOT NULL UNIQUE,  -- 추가: 불변 식별자
    github_login                  VARCHAR(100) NOT NULL UNIQUE,
    display_name                  VARCHAR(150),
    avatar_url                    VARCHAR(500),
    email                         VARCHAR(200),
    github_access_token_encrypted TEXT,                          -- 추가: Fernet 암호화된 토큰
    created_at                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 추가 이유
- **`github_id`**: GitHub `login`은 사용자가 언제든 변경할 수 있으므로, 불변 식별자인 숫자 ID를 upsert 키로 사용해야 합니다. 기존 스키마의 `github_login UNIQUE`는 display용으로 유지.
- **`github_access_token_encrypted`**: 2단계(레포 조회)에서 `GET /user/repos`를 호출하려면 access_token이 필요합니다. 평문 저장은 위험하므로 Fernet(AES-128-CBC + HMAC-SHA256)으로 암호화. 별도 테이블로 빼는 것보다 1:1 관계라 같은 테이블에 두는 편이 단순함.

### 테이블은 언제 만들어지나
`app/main.py`의 lifespan에서 시작 시 `Base.metadata.create_all`을 호출합니다. DB가 안 떠 있으면 경고만 출력하고 서버는 정상 기동합니다 (개발 편의).

---

## 4. 환경변수 (.env)

```env
# DB (나중에 붙일 때 이 URL로)
DATABASE_URL=postgresql+asyncpg://ciuser:cipass@localhost:5432/cidb

# GitHub OAuth App 등록 후 받는 값
GITHUB_CLIENT_ID=Ov23li...
GITHUB_CLIENT_SECRET=4672ac...
GITHUB_REDIRECT_URI=http://127.0.0.1:8000/auth/github/callback
GITHUB_OAUTH_SCOPES=read:user user:email repo

# 로그인 완료 후 프론트(또는 임시 success 페이지)로 리다이렉트
FRONTEND_REDIRECT_URL=http://127.0.0.1:8000/auth/success

# JWT
JWT_SECRET=<강한 랜덤값>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080

# 토큰 암호화 키 (미설정 시 JWT_SECRET에서 SHA-256 파생)
TOKEN_ENCRYPTION_KEY=
```

---

## 5. GitHub OAuth App 등록 (딱 한 번)

1. https://github.com/settings/developers 접속
2. 좌측 **OAuth Apps** → **New OAuth App**
3. 폼 작성:
   - **Application name**: `CI-CD-Back` (임의)
   - **Homepage URL**: `http://127.0.0.1:8000`
   - **Authorization callback URL**: `http://127.0.0.1:8000/auth/github/callback` ← **정확히 일치해야 함**
4. **Register application**
5. 생성 화면에서:
   - **Client ID** 복사 → `.env`의 `GITHUB_CLIENT_ID`
   - **Generate a new client secret** 클릭 → 나오는 값 복사 → `.env`의 `GITHUB_CLIENT_SECRET`

⚠️ Secret은 한 번만 보여지므로 반드시 즉시 복사해서 `.env`에 저장.

---

## 6. 실행 방법

### 원클릭 (권장)
```
run_login.cmd
```

이 스크립트가 하는 일:
1. `.venv` 없으면 생성
2. `requirements.txt` 설치
3. `.env` 없으면 대화식 생성 (Client ID/Secret 입력 받음)
4. 포트 8000 점유 중인 프로세스 자동 강제 종료
5. `__pycache__` 정리
6. uvicorn 기동 (`--reload`)
7. 5초 뒤 브라우저 자동 오픈 (`/auth/github/login`)

다른 포트로 띄우려면:
```
run_login.cmd 8001
```
(단, GitHub OAuth App의 callback URL과 `.env`의 `GITHUB_REDIRECT_URI`도 같이 바꿔야 함)

### 수동 실행
```
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## 7. 로그인 테스트

1. 브라우저에서 http://127.0.0.1:8000/auth/github/login 접속
2. GitHub 승인 페이지로 자동 이동 → 본인 GitHub ID/비번 입력 → **Authorize**
3. `/auth/success` 페이지로 돌아옴 → JWT 토큰 박스 표시
4. **[/auth/me 호출]** 버튼 클릭 → 본인 프로필 JSON 출력

### JWT 수동 확인
```bash
curl -H "Authorization: Bearer <JWT>" http://127.0.0.1:8000/auth/me
```

### Swagger UI에서 확인
1. http://127.0.0.1:8000/docs 접속
2. 우측 상단 **Authorize** 버튼 → `Bearer <JWT>` 입력
3. `/auth/me` → **Try it out** → **Execute**

---

## 8. DB 없이 동작하는 이유

개발 초기에 Postgres 설치/세팅이 귀찮을 수 있어, DB 없이도 로그인 플로우가 JWT 발급까지 완주하도록 설계했습니다.

| 컴포넌트 | DB 있을 때 | DB 없을 때 |
|---------|-----------|-----------|
| 서버 기동 | `users` 테이블 자동 생성 | 경고 로그만 출력, 정상 기동 |
| `/auth/github/callback` | users upsert + JWT 발급 | upsert 스킵, 프로필을 JWT 클레임에 embed |
| `/auth/me` | DB 조회 후 응답 (`source: "db"`) | JWT 클레임에서 복원 (`source: "jwt"`) |
| access_token 보관 | 암호화해서 users에 저장 | **저장 안 됨** (2단계 레포 조회 불가) |

→ 로그인 "기능 확인"은 DB 없이 가능. 실제로 사용자 데이터를 쌓거나 레포를 불러오는 단계부터는 DB 필수.

### 나중에 DB 붙이는 법
1. PostgreSQL 15+ 준비:
   ```bash
   docker run -d --name cidb-pg \
     -e POSTGRES_USER=ciuser -e POSTGRES_PASSWORD=cipass -e POSTGRES_DB=cidb \
     -p 5432:5432 postgres:15
   ```
2. `run_login.cmd` 재시작 → 로그에 `[startup] DB connected, tables ensured.` 뜨면 OK
3. 재로그인하면 `users` 테이블에 persist됨, 이후 `/auth/me`도 `source: "db"`로 응답

---

## 9. 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/auth/github/login` | state 쿠키 세팅 후 GitHub 승인 페이지로 302 |
| GET | `/auth/github/callback` | state 검증 → 토큰 교환 → 프로필 조회 → users upsert → JWT 발급 → FRONTEND_REDIRECT_URL로 302 |
| GET | `/auth/me` | Bearer JWT로 현재 사용자 프로필 반환 |
| GET | `/auth/success` | JWT 표시용 임시 HTML 페이지 (프론트 구현 전까지 사용) |
| GET | `/health` | 헬스체크 |
| GET | `/docs` | Swagger UI |

---

## 10. 보안 고려사항

- **state 쿠키**: CSRF 방지. `httponly`, `samesite=lax`, 10분 TTL. 콜백에서 `secrets.compare_digest`로 비교.
- **access_token 저장**: Fernet(AES-CBC + HMAC) 암호화. 키는 `TOKEN_ENCRYPTION_KEY` 환경변수, 미설정 시 `JWT_SECRET`에서 SHA-256 파생. 프로덕션에서는 반드시 별도 키로 분리할 것.
- **JWT**: HS256. `sub`, `gid`(github_id), 프로필 일부(login, name, avatar, email), `iat`, `exp`. 기본 7일 만료.
- **scope**: `read:user user:email repo`. `repo`는 2단계에서 프라이빗 레포 읽기용. 필요 없으면 축소 가능.
- **secure 쿠키**: 로컬 HTTP라 `secure=False`. HTTPS 배포 시 `True`로 바꿀 것.

---

## 11. 작업 중 겪은 트러블슈팅

### 문제 1: `/auth/github/login`에서 404
**증상**: 서버는 정상 기동되고 `/docs`도 뜨는데 `/auth/*` 라우트만 404.

**원인**: 첫 번째 `run_server.cmd` 실행으로 생긴 uvicorn 워커 프로세스(PID 33628)가 Ctrl+C로 죽지 않고 좀비 상태로 살아남아 포트 8000을 점유. 이후 실행한 새 uvicorn은 별도 PID로 떴지만, Windows가 요청을 좀비 프로세스로 라우팅해서 옛날 코드(auth 라우터 없던 시절)의 응답이 나왔음.

**진단 방법**:
```bash
# 1. 파이썬으로 직접 import해서 라우트 개수 확인 (12개여야 정상)
.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes))"

# 2. 실행 중인 서버의 라우트와 비교
curl -s http://127.0.0.1:8000/openapi.json | python -m json.tool | grep -A1 '"paths"'

# 3. 포트 8000 점유 PID 확인
netstat -ano | findstr :8000 | findstr LISTENING

# 4. 좀비 PID 찾기
tasklist | grep python
```

**해결**:
```bash
taskkill /F /PID <좀비PID>
```

**재발 방지**: `run_login.cmd`에 포트 8000 점유 프로세스 자동 강제 종료 로직 추가 완료:
```cmd
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
```

### 문제 2: `redirect_uri_mismatch`
**원인**: GitHub OAuth App의 Authorization callback URL과 `.env`의 `GITHUB_REDIRECT_URI`가 불일치.

**해결**: 포트, 슬래시, `localhost` vs `127.0.0.1`까지 정확히 맞출 것.

### 문제 3: `invalid oauth state`
**원인**: `/auth/github/login`을 거치지 않고 `/auth/github/callback`을 직접 호출했거나, 브라우저가 쿠키를 차단.

**해결**: 반드시 `/auth/github/login`부터 시작. 시크릿 창에서 쿠키 차단 여부 확인.

---

## 12. 다음 단계 (2단계 예고)

### 목표
로그인된 사용자의 access_token으로 `GET https://api.github.com/user/repos`를 호출, 결과를 `repositories` 테이블에 저장.

### 필요한 작업
1. **Postgres 실제 기동** (docker run 또는 로컬 설치)
2. **`repositories` 테이블 생성** — 이미 스키마 문서에 있음, SQLAlchemy 모델만 추가하면 됨
3. **`/api/repos/sync` 엔드포인트 추가**:
   - `get_current_user` 의존성으로 현재 사용자 식별
   - DB에서 암호화된 access_token 꺼내서 복호화
   - GitHub API 호출 (페이지네이션 고려, `per_page=100`)
   - `repositories` 테이블에 upsert
4. **`/api/repos` 엔드포인트 추가** — 현재 사용자의 레포 목록 조회

### 해결해야 할 이슈
- **access_token 만료 처리**: GitHub OAuth 토큰은 기본적으로 만료 없음이지만, 사용자가 Revoke할 수 있음. 401 응답 시 재로그인 유도.
- **페이지네이션**: 레포가 100개 넘으면 `Link` 헤더 파싱 필요.
- **성능**: 동기화는 비동기 백그라운드 작업으로 돌리는 게 좋을 수 있음 (BackgroundTasks 또는 Celery).

---

## 부록 A. 파일별 한줄 요약

```
app/
├── main.py                # FastAPI 앱 + lifespan + 라우터 include
├── config.py              # Pydantic Settings (.env 로드)
├── db.py                  # SQLAlchemy async 엔진 + Base + get_db
├── db_models.py           # User ORM 모델
├── models.py              # (기존) 파이프라인 관련 Pydantic 모델
├── service.py             # (기존) Ubuntu SSH trigger + 결과 저장
└── auth/
    ├── __init__.py
    ├── crypto.py          # Fernet encrypt/decrypt
    ├── jwt_utils.py       # JWT 발급/검증 + get_current_user 의존성
    ├── github_oauth.py    # authorize URL / code 교환 / 프로필 조회
    └── router.py          # /auth/* 라우터 (login, callback, me, success)
```

---

## 부록 B. 빠른 체크리스트

- [ ] Python 3.11+ 설치됨
- [ ] GitHub OAuth App 등록 완료 (callback URL: `http://127.0.0.1:8000/auth/github/callback`)
- [ ] `.env`에 `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `JWT_SECRET` 채워짐
- [ ] `run_login.cmd` 실행 → `Application startup complete.` 로그 확인
- [ ] 브라우저에서 `/auth/github/login` → GitHub 승인 → `/auth/success`에서 JWT 확인
- [ ] **[/auth/me 호출]** 버튼 → 본인 프로필 JSON 출력
