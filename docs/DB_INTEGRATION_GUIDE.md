# CI/CD Database 통합 가이드

**작성일**: 2026-04-09  
**상태**: ✅ 완성 (즉시 구현 가능)

---

## 📚 문서 구조

```
docs/
├── 01_DB_FUNCTIONAL_SPEC.md    ← DB 기능 요구사항 ("무엇을 저장할 것인가")
├── 02_DB_SCHEMA_DESIGN.md      ← DB 스키마 설계 ("어떻게 구현할 것인가")
├── schema.sql                   ← PostgreSQL DDL 스크립트 (바로 실행 가능)
└── DB_INTEGRATION_GUIDE.md     ← 이 파일 (팀 온보딩용)
```

---

## 🎯 한눈에 보는 구조

### DB 관계도 (ER Diagram)

```
┌──────────────────────┐
│   pipeline_jobs      │  ← 모든 데이터의 중심
│  (CI/CD 작업 기록)   │
└──────────┬───────────┘
           │
    ┌──────┼──────────────────────┬────────────────┐
    │      │                      │                │
    ▼      ▼                      ▼                ▼
┌──────┐ ┌────────────┐ ┌─────────────────┐ ┌──────────────┐
│Steps │ │   Logs     │ │Security Findings│ │  Artifacts   │
│(7개) │ │ (상세로그) │ │(gitleaks,       │ │  (빌드 결과) │
│      │ │            │ │ semgrep)        │ │              │
└──────┘ └────────────┘ └─────────────────┘ └──────┬───────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │  Deployments    │
                                          │ (배포 이력)     │
                                          └─────────────────┘
```

### 주요 7개 테이블

| # | 테이블명 | 용도 | 행 수/월 | 설명 |
|-|-|-|-|-|
| 1 | `pipeline_jobs` | **작업 추적** | ~1,000 | 각 CI/CD 실행의 최상위 정보 |
| 2 | `pipeline_steps` | **단계별 진행** | ~7,000 | clone, test, build 등 각 Step 결과 |
| 3 | `step_logs` | **상세 로그** | ~70,000 | 각 Step의 stdout/stderr |
| 4 | `security_findings` | **보안 이슈** | ~5,000 | gitleaks/semgrep 취약점 |
| 5 | `build_artifacts` | **빌드 산출물** | ~1,000 | JAR, Docker, 실행파일 |
| 6 | `deployments` | **배포 기록** | ~500 | staging/production 배포 |
| 7 | `security_summary` | **요약 (캐시)** | ~1,000 | 보안 결과 빠른 조회용 |

---

## 🚀 빠른 시작 (5분)

### 1️⃣ PostgreSQL 설치

**Windows (PowerShell)**:
```powershell
# 또는 PostgreSQL 공식 설치 프로그램 사용
choco install postgresql
```

**Ubuntu/Linux**:
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl start postgresql
```

### 2️⃣ DB 생성 및 스키마 적용

```bash
# schema.sql 실행
psql -U postgres -h localhost -f docs/schema.sql

# 또는 직접 실행
psql -U postgres -h localhost
\c cicd_engine
\i docs/schema.sql
```

### 3️⃣ 테이블 확인

```bash
psql -U postgres -d cicd_engine

# 테이블 목록
\dt

# 특정 테이블 스키마
\d pipeline_jobs

# 모든 인덱스
\di
```

---

## 📊 데이터 흐름

### 전체 파이프라인

```
[Client/Web]
    ↓
    ├─ repo URL + branch 선택
    │
[FastAPI Backend]
    ↓
    ├─ job_id 생성
    ├─ status = "queued" → DB 저장
    ├─ Ubuntu CI엔진으로 전달 (비동기)
    │
[Ubuntu CI Engine]
    ↓
    ├─ clone (step_1)
    ├─ install (step_2)
    ├─ lightweight_security_scan (step_3) ← gitleaks findings → DB
    ├─ test (step_4) ← test logs → DB
    ├─ deep_security_scan (step_5) ← semgrep findings → DB
    ├─ build (step_6) ← artifact metadata → DB
    ├─ deploy (step_7) ← deployment status → DB
    │
    └─ POST /get-results (전체 결과 전송)
    │
[DB 저장]
    ├─ pipeline_jobs.status = "success" | "failed"
    ├─ pipeline_steps = 7개 결과 저장
    ├─ step_logs = 상세 로그 저장
    ├─ security_findings = 취약점 저장
    ├─ build_artifacts = 아티팩트 메타데이터 저장
    ├─ deployments = 배포 결과 저장
    └─ security_summary = 캐시 업데이트
    │
[Backend API]
    ├─ /get-results?job_id=xxx (조회)
    └─ 최종 결과 Client로 전송
```

---

## 🔌 Backend 통합 (FastAPI)

### 현재 상태
```python
# app/main.py (현재)
result_store = ResultStore()  # ← 메모리 저장소
```

### 변경할 사항
```python
# app/main.py (수정 필요)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import PipelineJob, PipelineStep, SecurityFinding

# DB 연결
DATABASE_URL = "postgresql://user:password@localhost/cicd_engine"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

# endpoint /start-pipeline 수정
@app.post("/start-pipeline")
def start_pipeline(req: StartPipelineRequest) -> StartPipelineResponse:
    job = PipelineJob(
        repo_url=str(req.repo_url),
        branch=req.branch,
        trigger_source=req.trigger_source,
        status="queued"
    )
    db.add(job)
    db.commit()
    return StartPipelineResponse(job_id=str(job.job_id), status="triggered")
```

### 구현 순서
1. **SQLAlchemy Models 작성** (`app/models.py` 확장)
2. **DB 접근 계층** (`app/db.py` 신규)
3. **API 엔드포인트 수정** (`app/main.py`)
4. **테스트** (`tests/test_db.py`)

---

## 🔒 보안 스캔 결과 저장 예시

### gitleaks 결과 저장
```json
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440002",
    "scan_type": "gitleaks",
    "severity": "critical",
    "rule_id": "slack-bot-token",
    "file_path": "config/.env",
    "line_number": 5,
    "message": "Slack Bot Token detected",
    "is_masked": true
}
```

### semgrep 결과 저장
```json
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440004",
    "scan_type": "semgrep",
    "severity": "high",
    "rule_id": "python.lang.best-practice.use-of-assert",
    "rule_name": "Use of assert statement",
    "file_path": "app/service.py",
    "line_number": 150,
    "cvss_score": 5.3,
    "cwe_id": "CWE-506"
}
```

---

## 📈 성능 기준

| 작업 | 기대 응답 시간 | 인덱스 |
|----|-------------|--------|
| Job 목록 조회 (100개) | < 100ms | `created_at DESC` |
| 보안 결과 검색 (severity) | < 200ms | `job_id, severity` |
| 로그 조회 (1000줄) | < 500ms | `step_id, timestamp` |
| 통계 집계 | < 1s | date range |

---

## 🗂 파일구조 최종안

```
capstone/
├── Capstone-Back/
│   ├── app/
│   │   ├── main.py           (Backend API)
│   │   ├── models.py         (Pydantic models - 수정 필요)
│   │   ├── db.py             (SQLAlchemy models - 신규)
│   │   ├── database.py       (DB 연결 - 신규)
│   │   └── service.py        (비즈니스 로직)
│   ├── tests/
│   │   └── test_db.py        (DB 테스트 - 신규)
│   ├── docs/
│   │   ├── 01_DB_FUNCTIONAL_SPEC.md  ✅ 완성
│   │   ├── 02_DB_SCHEMA_DESIGN.md    ✅ 완성
│   │   ├── schema.sql                ✅ 완성 (즉시 실행 가능)
│   │   └── DB_INTEGRATION_GUIDE.md   ✅ 이 파일
│   ├── requirements.txt       (+ sqlalchemy, psycopg2)
│   └── ...
```

---

## 📝 체크리스트

### Phase 1: DB 준비 (1주)
- [ ] PostgreSQL 설치
- [ ] `schema.sql` 실행
- [ ] 테이블/인덱스 생성 확인
- [ ] 샘플 데이터 삽입 확인

### Phase 2: Backend 통합 (1주)
- [ ] SQLAlchemy Models 작성
  - [ ] `app/db.py` 신규 생성
  - [ ] `PipelineJob`, `PipelineStep`, `SecurityFinding` 클래스
- [ ] DB 접근 계층
  - [ ] `app/database.py` 신규 생성
  - [ ] Connection Pool 설정
- [ ] API 수정
  - [ ] `/start-pipeline` → DB 저장
  - [ ] `/get-results` → DB 조회
- [ ] 기본 테스트

### Phase 3: 보안 기능 (1주)
- [ ] gitleaks 결과 저장 함수
- [ ] semgrep 결과 저장 함수
- [ ] security_summary 자동 업데이트 (trigger)
- [ ] 보안 대시보드 API

### Phase 4: 로깅 & 모니터링 (1주)
- [ ] 로그 배치 저장 함수
- [ ] 로그 스트리밍 API (optional: WebSocket)
- [ ] 에러 로깅

### Phase 5: 최적화 (1주)
- [ ] 쿼리 성능 테스트
- [ ] 인덱스 추가 최적화
- [ ] 캐싱 전략 (Redis optional)

---

## 🆘 FAQ & 문제 해결

### Q1. PostgreSQL 접속 오류
```
psql: error: could not translate host name "localhost" to address
```
**A**: PostgreSQL 서버 시작 확인
```bash
# Windows
pg_isready -h localhost

# Ubuntu
sudo systemctl status postgresql
sudo systemctl restart postgresql
```

### Q2. 권한 오류
```
FATAL: password authentication failed for user "postgres"
```
**A**: 비밀번호 확인 또는 초기화
```bash
# Windows
psql -U postgres
# Edit C:\Program Files\PostgreSQL\14\data\pg_hba.conf

# Ubuntu
sudo -u postgres psql
```

### Q3. schema.sql 실행 실패
```
psql: error: connection to server at "localhost" (127.0.0.1), port 5432 failed
```
**A**: 파일 경로 및 권한 확인
```bash
# 절대 경로 사용
psql -U postgres -f /absolute/path/to/schema.sql
```

---

## 📞 팀 커뮤니케이션

### 공유 방법

1. **GitHub에 푸시**
   ```bash
   git add docs/
   git commit -m "docs: Add DB schema and functional spec"
   git push origin develop
   ```

2. **팀 공지**
   - Slack: "DB 스키마 설계 완료! docs/ 폴더를 참고해주세요"
   - 링크: `Capstone-Back/docs/`

3. **팀 미팅**
   - ER 다이어그램 공유
   - schema.sql 데모 (5분)
   - Q&A

---

## 🔗 참고 자료

- PostgreSQL 공식 문서: https://www.postgresql.org/docs/
- SQLAlchemy ORM: https://docs.sqlalchemy.org/
- Pydantic: https://docs.pydantic.dev/
- FastAPI + SQLAlchemy: https://fastapi.tiangolo.com/advanced/sql-databases/

---

## ✅ 완료 사항

- ✅ DB 기능 명세서 작성 (`01_DB_FUNCTIONAL_SPEC.md`)
- ✅ DB 스키마 설계 (`02_DB_SCHEMA_DESIGN.md`)
- ✅ PostgreSQL DDL 스크립트 (`schema.sql`) - **바로 실행 가능**
- ✅ ER 다이어그램 시각화
- ✅ 팀 온보딩 가이드 (이 파일)

---

**다음 단계**: Backend 개발자는 Phase 2 시작 가능합니다! 🚀

**문의**: DB 관련 질문은 이 가이드 또는 02_DB_SCHEMA_DESIGN.md 참고 

**마지막 업데이트**: 2026-04-09
