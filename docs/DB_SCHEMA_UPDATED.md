# CI/CD 파이프라인 데이터베이스 스키마 설계 (개정판)

**문서 범위**: GitHub 기반 CI/CD 파이프라인 DB 설계  
**대상**: Backend 개발팀  
**작성일**: 2026-04-10  
**DB**: PostgreSQL 또는 MySQL (RDS 호환)

---

## 📋 1부. DB 기능 명세

### 1.1 사용자 관리 (Users)

#### Create - 사용자 등록

def register_user(
    github_login: str,
    avatar_url: str = None
) -> dict:
    """
    GitHub 사용자/조직 등록
    
    Returns:
        {
            "id": 1,
            "github_login": "moddak2",
            "avatar_url": "https://...",
            "created_at": "2026-04-10T10:00:00Z"
        }
    """

#### Read - 사용자 조회

def get_user(user_id: int) -> dict:
    """
    특정 사용자 정보 조회
    """

def get_user_repos(user_id: int) -> list[dict]:
    """
    특정 사용자의 모든 레포지토리 조회
    """

---

### 1.2 레포지토리 관리 (Repositories)

#### Create - 레포 등록

def register_repository(
    user_id: int,
    repo_name: str,
    repo_url: str,
    default_branch: str = 'main',
    runtime: str = None  # 'node','react','python','java','nextjs'
) -> dict:
    """
    새로운 레포지토리 등록
    
    Returns:
        {
            "id": 1,
            "user_id": 1,
            "repo_name": "capstone-back",
            "repo_url": "https://github.com/moddak2/capstone-back",
            "runtime": "python",
            "created_at": "2026-04-10T10:00:00Z"
        }
    """

#### Read - 레포 조회

def get_repository(repo_id: int) -> dict:
    """
    특정 레포지토리 상세 정보 조회
    """

def get_repository_by_name(user_id: int, repo_name: str) -> dict:
    """
    사용자 ID와 레포 이름으로 조회
    """

---

### 1.3 파이프라인 실행 관리 (Pipeline Runs)

#### Create - 파이프라인 시작

def start_pipeline_run(
    repository_id: int,
    branch: str,
    trigger_type: str  # 'webhook','manual','callback'
) -> dict:
    """
    새로운 파이프라인 실행 시작
    
    Returns:
        {
            "id": 1,
            "run_id": "run-20260410-001",
            "status": "queued",
            "created_at": "2026-04-10T10:00:00Z"
        }
    """

#### Update - 파이프라인 상태 업데이트

def update_run_status(
    run_id: str,
    status: str,  # 'queued','running','success','failed'
    started_at: datetime = None,
    finished_at: datetime = None
) -> bool:
    """
    파이프라인 실행 상태 업데이트
    """

#### Read - 파이프라인 조회

def get_pipeline_run(run_id: str) -> dict:
    """
    특정 파이프라인 실행 조회
    
    Returns:
        {
            "id": 1,
            "run_id": "run-20260410-001",
            "repository_id": 1,
            "branch": "develop",
            "status": "running",
            "trigger_type": "webhook",
            "started_at": "2026-04-10T10:00:05Z",
            "finished_at": null,
            "created_at": "2026-04-10T10:00:00Z"
        }
    """

def get_repo_recent_runs(repo_id: int, limit: int = 20) -> list[dict]:
    """
    특정 레포의 최근 파이프라인 실행 목록 (20개)
    """

---

### 1.4 파이프라인 Step 관리

#### Create - Step 실행 결과 기록

def record_step_result(
    pipeline_run_id: int,
    step_name: str,  # 'clone','install','test','deploy'
    status: str,
    exit_code: int = None,
    summary_message: str = None,
    log_path: str = None,
    started_at: datetime = None,
    finished_at: datetime = None
) -> bool:
    """
    각 파이프라인 Step의 실행 결과 기록
    """

#### Read - Step 결과 조회

def get_run_steps(pipeline_run_id: int) -> list[dict]:
    """
    특정 파이프라인 실행의 모든 Step 조회
    
    Returns:
        [
            {
                "id": 1,
                "step_name": "clone",
                "status": "success",
                "exit_code": 0,
                "summary_message": "Cloned repository...",
                "started_at": "2026-04-10T10:00:05Z",
                "finished_at": "2026-04-10T10:00:10Z"
            },
            ...
        ]
    """

def get_step_log(log_path: str) -> str:
    """
    S3 또는 로컬에서 step 로그 파일 조회
    """

---

### 1.5 보안 스캔 결과 (Security Findings)

#### Create - 보안 이슈 저장

def save_security_finding(
    pipeline_run_id: int,
    scanner_name: str,  # 'gitleaks','semgrep'
    scan_type: str,  # 'lightweight','deep'
    rule_id: str = None,
    severity: str = None,  # 'critical','high','medium','low'
    title: str = None,
    file_path: str = None,
    line_number: int = None,
    message: str = None,
    cvss_score: float = None
) -> bool:
    """
    보안 스캔 결과 (gitleaks + semgrep) 저장
    """

#### Read - 보안 이슈 조회

def get_run_findings(
    run_id: int,
    severity: str = None,
    scanner_name: str = None
) -> list[dict]:
    """
    특정 파이프라인 실행의 모든 보안 이슈 조회
    
    Returns:
        [
            {
                "id": 1,
                "scanner_name": "gitleaks",
                "severity": "critical",
                "title": "Slack Bot Token found",
                "file_path": "config/.env",
                "line_number": 5,
                "cvss_score": 9.8
            },
            ...
        ]
    """

def count_findings_by_severity(run_id: int) -> dict:
    """
    심각도별 이슈 개수 집계
    
    Returns:
        {
            "critical": 2,
            "high": 5,
            "medium": 3,
            "low": 1
        }
    """

---

### 1.6 배포 기록 (Deployments)

#### Create - 배포 기록

def record_deployment(
    pipeline_run_id: int,
    repository_id: int,
    artifact_hash: str,  # SHA256
    runtime: str,
    port: int = None,
    deploy_url: str = None,
    ec2_instance_id: str = None,
    status: str = 'success'  # 'success','failed','skipped'
) -> bool:
    """
    배포 이력 기록
    """

#### Read - 배포 조회

def get_deployment_history(
    repo_id: int,
    limit: int = 50
) -> list[dict]:
    """
    특정 레포의 배포 이력 조회
    
    Returns:
        [
            {
                "id": 1,
                "pipeline_run_id": 1,
                "artifact_hash": "abc123...",
                "runtime": "python",
                "deploy_url": "/moddak2/capstone-back/",
                "status": "success",
                "deployed_at": "2026-04-10T10:15:00Z"
            },
            ...
        ]
    """

def get_latest_deployment(repo_id: int) -> dict:
    """
    특정 레포의 최신 배포 조회
    """

---

### 1.7 콜백 로그 (Callback Logs)

#### Create - 콜백 전송 기록

def log_callback(
    pipeline_run_id: int,
    callback_url: str,
    delivered: bool = False,
    http_status: str = None,
    error_message: str = None,
    payload_path: str = None
) -> bool:
    """
    GitHub/외부 시스템으로의 콜백 전송 기록
    """

#### Update - 콜백 재시도

def retry_callback(
    callback_log_id: int,
    max_attempts: int = 3
) -> bool:
    """
    실패한 콜백 재시도
    """

#### Read - 콜백 로그 조회

def get_run_callbacks(run_id: int) -> list[dict]:
    """
    특정 파이프라인 실행의 모든 콜백 기록 조회
    
    Returns:
        [
            {
                "id": 1,
                "callback_url": "https://github.com/webhook",
                "delivered": true,
                "attempts": 1,
                "http_status": "200",
                "created_at": "2026-04-10T10:15:30Z"
            },
            ...
        ]
    """

---

## 📊 2부. DB 스키마 설계

### 2.1 테이블 정의

#### users

CREATE TABLE users (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    github_login      VARCHAR(100) NOT NULL UNIQUE,
    avatar_url        VARCHAR(500),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_login (github_login)
);

Comment: GitHub 사용자/조직 정보

#### repositories

CREATE TABLE repositories (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id),
    repo_name         VARCHAR(200) NOT NULL,
    repo_url          VARCHAR(500) NOT NULL,
    default_branch    VARCHAR(100) DEFAULT 'main',
    runtime           VARCHAR(50),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(user_id, repo_name),
    INDEX idx_user (user_id),
    INDEX idx_repo_name (repo_name)
);

Comment: 등록된 GitHub 레포지토리

#### pipeline_runs

CREATE TABLE pipeline_runs (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id            VARCHAR(100) NOT NULL UNIQUE,
    repository_id     BIGINT NOT NULL REFERENCES repositories(id),
    branch            VARCHAR(100) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'queued',
    trigger_type      VARCHAR(50),
    started_at        TIMESTAMP NULL,
    finished_at       TIMESTAMP NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_repo_run (repository_id, created_at DESC),
    INDEX idx_status (status),
    INDEX idx_run_id (run_id)
);

Comment: 파이프라인 실행 기록

#### pipeline_steps

CREATE TABLE pipeline_steps (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    pipeline_run_id   BIGINT NOT NULL REFERENCES pipeline_runs(id),
    step_name         VARCHAR(100) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'pending',
    exit_code         INT,
    summary_message   TEXT,
    log_path          VARCHAR(500),
    started_at        TIMESTAMP NULL,
    finished_at       TIMESTAMP NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_run_step (pipeline_run_id, step_name),
    INDEX idx_run_status (pipeline_run_id, status)
);

Comment: 각 파이프라인 Step 실행 결과

#### security_findings

CREATE TABLE security_findings (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    pipeline_run_id   BIGINT NOT NULL REFERENCES pipeline_runs(id),
    scanner_name      VARCHAR(50) NOT NULL,
    scan_type         VARCHAR(50) NOT NULL,
    rule_id           VARCHAR(200),
    severity          VARCHAR(20) NOT NULL,
    title             VARCHAR(500),
    file_path         VARCHAR(500),
    line_number       INT,
    message           TEXT,
    cvss_score        DECIMAL(3,1),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_run_scanner (pipeline_run_id, scanner_name),
    INDEX idx_severity (severity),
    INDEX idx_rule (rule_id)
);

Comment: gitleaks + semgrep 보안 스캔 결과

#### deployments

CREATE TABLE deployments (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    pipeline_run_id   BIGINT REFERENCES pipeline_runs(id),
    repository_id     BIGINT NOT NULL REFERENCES repositories(id),
    artifact_hash     VARCHAR(64) NOT NULL,
    runtime           VARCHAR(50) NOT NULL,
    port              INT,
    deploy_url        VARCHAR(500),
    ec2_instance_id   VARCHAR(50),
    status            VARCHAR(20) NOT NULL,
    deployed_at       TIMESTAMP NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_repo_deploy (repository_id, deployed_at DESC),
    INDEX idx_artifact (artifact_hash)
);

Comment: 배포 이력

#### callback_logs

CREATE TABLE callback_logs (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    pipeline_run_id   BIGINT NOT NULL REFERENCES pipeline_runs(id),
    callback_url      VARCHAR(500),
    delivered         BOOLEAN NOT NULL DEFAULT FALSE,
    attempts          INT DEFAULT 0,
    http_status       VARCHAR(10),
    error_message     TEXT,
    payload_path      VARCHAR(500),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_run_callback (pipeline_run_id),
    INDEX idx_delivered (delivered)
);

Comment: 콜백 전송 기록

---

### 2.2 인덱스 전략

**Primary 인덱스**:
- users.github_login (UNIQUE)
- repositories.user_id + repo_name (UNIQUE)
- pipeline_runs.run_id (UNIQUE)

**Composite 인덱스**:
- repositories: (user_id, repo_name)
- pipeline_runs: (repository_id, created_at DESC)
- pipeline_steps: (pipeline_run_id, step_name)
- security_findings: (pipeline_run_id, scanner_name)
- deployments: (repository_id, deployed_at DESC)

**성능 목표**:
- 사용자 조회: < 10ms
- 레포 조회: < 20ms
- 파이프라인 목록: < 50ms
- Step 조회: < 30ms
- 보안 이슈 검색: < 100ms

---

### 2.3 데이터 관계도 (ERD)

users (1) ──────── (N) repositories
                        │
                        │ (1)
                        │
                        └──── (N) pipeline_runs ──── (N) pipeline_steps
                                    │
                                    ├─── (N) security_findings
                                    ├─── (N) deployments
                                    └─── (N) callback_logs

---

### 2.4 샘플 데이터

#### users

INSERT INTO users (github_login, avatar_url) VALUES
('moddak2', 'https://avatars.githubusercontent.com/u/xxx'),
('NekoNyangYee', 'https://avatars.githubusercontent.com/u/yyy');

#### repositories

INSERT INTO repositories (user_id, repo_name, repo_url, runtime) VALUES
(1, 'capstone-back', 'https://github.com/moddak2/capstone-back', 'python'),
(1, 'capstone-front', 'https://github.com/moddak2/capstone-front', 'react');

#### pipeline_runs

INSERT INTO pipeline_runs (run_id, repository_id, branch, status, trigger_type) VALUES
('run-20260410-001', 1, 'develop', 'success', 'webhook');

#### pipeline_steps

INSERT INTO pipeline_steps (pipeline_run_id, step_name, status, exit_code) VALUES
(1, 'clone', 'success', 0),
(1, 'install', 'success', 0),
(1, 'test', 'success', 0),
(1, 'deploy', 'success', 0);

#### security_findings

INSERT INTO security_findings (pipeline_run_id, scanner_name, severity, title) VALUES
(1, 'gitleaks', 'critical', 'Slack Bot Token detected'),
(1, 'semgrep', 'high', 'Use of assert in production');

#### deployments

INSERT INTO deployments (pipeline_run_id, repository_id, artifact_hash, runtime, status) VALUES
(1, 1, 'abc123def456...', 'python', 'success');

---

### 2.5 DB 선택 가이드

| 기준 | PostgreSQL | MySQL |
|------|-----------|-------|
| 확장성 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| JSON 지원 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| RDS 비용 | 표준 | 저렴 |
| 권장 | ✅ 트래픽 많음 | 초기 단계 |

---

### 2.6 저장 용량 추정 (월간)

| 테이블 | 행/월 | 크기/월 | 비율 |
|-------|------|--------|------|
| users | 50 | 10KB | 0.01% |
| repositories | 200 | 50KB | 0.05% |
| pipeline_runs | 2,600 | 200KB | 0.2% |
| pipeline_steps | 10,400 | 800KB | 0.8% |
| security_findings | 5,000 | 1MB | 1% |
| deployments | 1,300 | 200KB | 0.2% |
| callback_logs | 2,600 | 300KB | 0.3% |
| **합계** | **22,150** | **~2.5MB** | **100%** |

연간: 약 30MB (매우 가벼움 ✅)

---

**마지막 업데이트**: 2026-04-10
**문서 버전**: v2.0-final
**상태**: ✅ 설계 완료, Docker 구축 준비됨
