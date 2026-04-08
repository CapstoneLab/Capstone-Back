# CI-CD-Back

Windows에서 GitHub 레포 주소를 받아 Ubuntu CI 엔진을 실행하고, 결과를 다시 수신/저장하는 FastAPI 백엔드입니다.

## 핵심 엔드포인트

- `POST /start-pipeline` : 레포 주소를 받아 Ubuntu 파이프라인 시작
- `POST /get-results` : Ubuntu가 결과 JSON 콜백 전송
- `GET /get-results?job_id=...` : 저장된 결과 조회
- `GET /health` : 상태 확인

## 통신 방식

기본은 `SSH 트리거 + HTTP 콜백`입니다.

1. Windows API 서버가 `POST /start-pipeline` 요청 수신
2. 백엔드가 SSH로 Ubuntu의 `ubuntu_runner.py` 실행
3. Ubuntu가 CI 실행 후 `POST /get-results`로 결과 전송
4. Windows는 `results/<job_id>.json`으로 저장

원하면 `UBUNTU_TRIGGER_MODE=http`로 바꿔 Ubuntu 에이전트 HTTP 호출도 가능합니다.

## 빠른 시작

### 1) 환경 설정

```powershell
copy .env.example .env
```

`.env`에서 아래 값은 반드시 수정:

- `UBUNTU_SSH_HOST`
- `UBUNTU_SSH_USER`
- `WINDOWS_CALLBACK_BASE_URL` (Ubuntu에서 접근 가능한 Windows 주소)

Ubuntu CI 엔진 실행이 아래 형태라면 추가로 설정:

- `UBUNTU_RUNNER_PATH=/home/capstone/CI-CD-pipiline/main.py`
- `UBUNTU_RUNNER_REPO_ARG=repo`
- `UBUNTU_WORKING_DIR=/home/capstone/CI-CD-pipiline`
- `UBUNTU_PYTHON_COMMAND=python3`
- `UBUNTU_SHELL_PRELUDE=export PATH="$HOME/.local/bin:$PATH" && export NVM_DIR="$HOME/.nvm" && source "$NVM_DIR/nvm.sh"`

### 2) 의존성 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3) 서버 실행

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8010
```

### 4) API 호출 예시

```powershell
curl -X POST http://127.0.0.1:8010/start-pipeline `
  -H "Content-Type: application/json" `
  -d '{"repo_url":"https://github.com/owner/repo.git","branch":"main"}'
```

결과 조회:

```powershell
curl "http://127.0.0.1:8010/get-results?job_id=<JOB_ID>"
```

## 터미널 프롬프트 실행(레포 입력 자동화)

백엔드 서버를 먼저 실행한 뒤, 아래 스크립트로 레포 입력부터 결과 조회까지 한 번에 진행할 수 있습니다.

```powershell
c:/Users/suhodang1/CI-CD-Back/.venv/Scripts/python.exe run_pipeline_prompt.py
```

인자를 함께 주는 예시:

```powershell
c:/Users/suhodang1/CI-CD-Back/.venv/Scripts/python.exe run_pipeline_prompt.py --repo https://github.com/moddak2/- --branch main --backend-url http://127.0.0.1:8010
```

주의: 클라이언트에서 `--backend-url http://0.0.0.0:8010` 형태는 사용하지 마세요. 로컬 호출은 `http://127.0.0.1:8010`를 사용해야 안정적으로 동작합니다.

기본값은 로그 전체를 출력합니다. 마지막 N줄만 보고 싶으면 `--tail-lines`를 사용합니다.

```powershell
c:/Users/suhodang1/CI-CD-Back/.venv/Scripts/python.exe run_pipeline_prompt.py --repo https://github.com/moddak2/- --tail-lines 100
```

## 테스트

```powershell
pytest -q
```

## Ubuntu 측 준비

`ubuntu/ubuntu_runner.py`를 Ubuntu에 배치하고 실행 권한/의존성 준비:

```bash
pip3 install -r requirements.txt
python3 ubuntu_runner.py --help
```

실제 CI 엔진 명령은 `ubuntu/ubuntu_runner.py`의 `run_ci` 함수에서 교체하세요.

## 문서

- 상세 아키텍처: `docs/architecture.md`
