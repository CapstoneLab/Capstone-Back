# Capstone Backend - CI/CD Pipeline

Windows CI Trigger Backend이 실행되는 FastAPI 프로젝트입니다.

## 📌 프로젝트 구조

```
Capstone-Back/
├── app/
│   ├── main.py           # FastAPI 애플리케이션
│   ├── models.py         # Pydantic 데이터 모델
│   ├── service.py        # 비즈니스 로직
│   └── config.py         # 설정
├── tests/
│   └── test_*.py         # 테스트 파일
├── docs/
│   ├── README.md         # 이 파일 (프로젝트 문서)
│   ├── DB_SUMMARY.md     # 👈 DB 설계 요약 (팀원들이 먼저 읽을 것)
│   ├── 01_DB_FUNCTIONAL_SPEC.md  # DB 기능 명세
│   ├── 02_DB_SCHEMA_DESIGN.md    # DB 스키마 설계
│   ├── schema.sql         # PostgreSQL DDL 스크립트
│   └── DB_INTEGRATION_GUIDE.md   # 구현 가이드
├── requirements.txt      # Python 의존성
└── README.md            # 프로젝트 문서 (이 파일)
```

---

## 🚀 빠른 시작

### 1. 환경 설정

```bash
# Python 가상환경 생성
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/Mac

# 의존성 설치
pip install -r requirements.txt
```

### 2. 애플리케이션 실행

```bash
python -m app.main
# 또는
uvicorn app.main:app --reload
```

### 3. API 테스트

```bash
# Health check
curl http://localhost:8000/health

# Pipeline 시작
curl -X POST http://localhost:8000/start-pipeline \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/example/repo.git", "branch": "main"}'
```

---

## 📚 문서

### 🎯 팀을 위한 문서

**[주의] 다음 문서부터 읽으세요:**

1. **[DB_SUMMARY.md](DB_SUMMARY.md)** ← START HERE
   - 팀원들을 위한 DB 설계 요약
   - 한눈에 보는 구조, FAQ, 역할별 가이드

2. **[01_DB_FUNCTIONAL_SPEC.md](01_DB_FUNCTIONAL_SPEC.md)**
   - DB 기능 명세 (무엇을 저장할 것인가)
   - 7가지 주요 기능, CRUD API 정의

3. **[02_DB_SCHEMA_DESIGN.md](02_DB_SCHEMA_DESIGN.md)**
   - DB 스키마 설계 (어떻게 구현할 것인가)
   - 7개 테이블 정의, 샘플 데이터, 인덱스 전략

4. **[schema.sql](schema.sql)** ⭐ 바로 실행 가능
   - PostgreSQL DDL 스크립트
   - 복사-붙여넣기로 5분이면 DB 생성 가능

5. **[DB_INTEGRATION_GUIDE.md](DB_INTEGRATION_GUIDE.md)**
   - Backend 개발자를 위한 구현 가이드
   - Python + FastAPI + SQLAlchemy 통합 방법

---

## 🔌 API 엔드포인트

### POST `/start-pipeline`
CI/CD 파이프라인을 시작합니다.

**요청:**
```json
{
  "repo_url": "https://github.com/CapstoneLab/Capstone-Back.git",
  "branch": "develop",
  "trigger_source": "web-ui"
}
```

**응답:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "triggered",
  "message": "pipeline was triggered on ubuntu"
}
```

---

### GET `/get-results`
파이프라인 실행 결과를 조회합니다.

**쿼리 파라미터:**
- `job_id` (required) - 작업 ID

**응답:**
```json
{
  "found": true,
  "data": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "repo_url": "https://github.com/CapstoneLab/Capstone-Back.git",
    "branch": "develop",
    "status": "success",
    "completed_at": "2026-04-09T10:15:30Z",
    "steps": [...]
  },
  "message": "ok"
}
```

---

### POST `/get-results`
Ubuntu CI 엔진에서 결과를 전송합니다. (내부용)

---

## 📊 현재 상태

- ✅ FastAPI Backend 기본 구조
- ✅ Windows에서 Ubuntu 파이프라인 트리거
- ✅ 메모리 기반 결과 저장 (임시)
- ⏳ PostgreSQL 데이터베이스 연동 (구현 중)
- ⏳ 보안 스캔 결과 저장
- ⏳ 배포 이력 관리

---

## 🔄 개발 로드맵

### Phase 1: DB 설계 ✅ 완료
- [x] 기능 명세
- [x] 스키마 설계
- [x] DDL 스크립트

### Phase 2: DB 연동 (다음)
- [ ] PostgreSQL 설치
- [ ] schema.sql 실행
- [ ] SQLAlchemy 모델 작성
- [ ] API 엔드포인트 수정

### Phase 3: 기능 확장
- [ ] 보안 스캔 결과 저장
- [ ] 로그 스트리밍
- [ ] 통계 API

### Phase 4: 배포
- [ ] Docker 컨테이너화
- [ ] Ubuntu CI 엔진 연동
- [ ] 운영 모니터링

---

## 👨‍💼 팀 역할

| 역할 | 담당자 | 작업 |
|-----|--------|------|
| Backend 개발 | - | Phase 2, 3 구현 |
| DB 설계 | - | ✅ 완료 (위 문서 참고) |
| CI 엔진 | - | Phase 2 대기, 결과 전송 |
| Ops/인프라 | - | PostgreSQL 설치 |
| PM | - | 일정 조율 |

---

## 📞 개발 관련 문의

- **DB 설계 관련**: [DB_SUMMARY.md](DB_SUMMARY.md) 또는 [DB_INTEGRATION_GUIDE.md](DB_INTEGRATION_GUIDE.md) 참고
- **API 문제**: `app/main.py`, `app/models.py` 확인
- **테스트 실행**: `pytest tests/`

---

## 📦 의존성

- FastAPI 0.115.0
- Uvicorn 0.30.6
- Pydantic 2.9.2
- SQLAlchemy (추가 예정)
- psycopg2 (추가 예정)

자세한 내용은 [requirements.txt](../requirements.txt) 참고

---

## 🔐 보안

- 민감한 정보(API 키, 토큰)는 `.env` 파일에서 관리
- gitleaks로 시크릿 유출 감시
- semgrep으로 코드 취약점 분석
- 자세한 보안 정책은 [DB_SUMMARY.md](DB_SUMMARY.md) 참고

---

## 📅 마지막 업데이트

- **2026-04-09**: DB 설계 완료, 팀 문서 작성

---

## 🎯 다음 단계

1. 팀원들은 [DB_SUMMARY.md](DB_SUMMARY.md) 읽기
2. Ops팀: PostgreSQL 설치
3. Backend팀: SQLAlchemy 모델 작성 시작
4. PM: 일정 조율 및 오너십 정의

**궁금한 점은 docs 폴더의 FAQ를 참고하세요!**
