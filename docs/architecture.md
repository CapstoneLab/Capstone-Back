# 아키텍처 및 통신 흐름

## 목표

Windows 백엔드가 레포 URL을 받아 Ubuntu CI 엔진을 실행하고, 실행 결과를 다시 Windows에 수집/저장한다.

## 컴포넌트

- Windows FastAPI 서버
  - `POST /start-pipeline`
  - `POST /get-results`
  - `GET /get-results`
- Ubuntu 실행기 (`ubuntu_runner.py`)
  - CI 엔진 실행
  - 결과를 JSON으로 구성해 Windows 콜백 호출

## 권장 통신 방식

### 1) 현재 구현: SSH 트리거 + HTTP 콜백

- 장점
  - Ubuntu에 별도 API 서버 없어도 동작
  - SSH 접근만 가능해도 바로 적용 가능
- 단점
  - SSH 연결 실패/키 관리 이슈에 민감

### 2) 확장 방식: HTTP 트리거 + HTTP 콜백

- 장점
  - 재시도/큐/관측성 확장에 유리
  - 명령 실행 제어가 쉬움
- 단점
  - Ubuntu에 수신용 API 에이전트 운영 필요

## 인증 방식

- Windows 외부 클라이언트 -> `POST /start-pipeline`
  - 헤더: `x-api-key`
- Ubuntu -> Windows `POST /get-results`
  - 헤더: `x-callback-token`
- SSH 인증
  - 공개키 기반 인증 권장 (password 로그인 비활성화 권장)

## 시퀀스

1. 클라이언트가 Windows `POST /start-pipeline` 호출
2. Windows가 `job_id` 생성
3. Windows가 Ubuntu 트리거
   - `UBUNTU_TRIGGER_MODE=ssh`이면 SSH로 원격 스크립트 실행
   - `UBUNTU_TRIGGER_MODE=http`이면 Ubuntu API 호출
4. Ubuntu가 CI 엔진 실행
5. Ubuntu가 결과 payload를 `POST /get-results`로 전송
6. Windows가 `results/<job_id>.json` 저장
7. 클라이언트가 `GET /get-results?job_id=...`로 조회

## 결과 데이터 스키마

```json
{
  "job_id": "uuid",
  "status": "success | failed | running",
  "repo_url": "https://github.com/...",
  "branch": "main",
  "started_at": "ISO-8601",
  "ended_at": "ISO-8601",
  "logs": ["..."],
  "metadata": {}
}
```

## 운영 체크리스트

- Windows에서 8000 포트 접근 가능하도록 방화벽 허용
- Ubuntu에서 Windows 콜백 URL 접근 가능 여부 확인
- SSH 키 기반 접속 확인
- API 키/토큰을 `.env`에서 강한 값으로 설정
- 결과 파일 저장 경로 권한 확인 (`RESULTS_DIR`)

## 통합 테스트 절차

1. Windows API 실행
2. Ubuntu 실행기 배치/검증
3. `POST /start-pipeline` 호출
4. Ubuntu 로그에서 CI 실행 확인
5. Windows `results/<job_id>.json` 생성 확인
6. `GET /get-results?job_id=...`로 결과 검증
