# CI/CD DB 기능 명세 & 스키마 설계

**문서 범위**: DB 기능 요구사항 + 스키마 정의만 집중  
**대상**: Backend 개발팀  
**작성일**: 2026-04-09

---

## 📋 1부. DB 기능 명세

### 1.1 파이프라인 작업 관리

#### Create - 파이프라인 시작

def create_pipeline_job(
    repo_url: str,
    branch: str,
    trigger_source: str  # "web-ui", "api", "scheduled"
) -> dict:
    """
    새로운 파이프라인 작업 생성
    
    Returns:
        {
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "created_at": "2026-04-09T10:00:00Z",
            "status": "queued"
        }
    """

#### Read - 파이프라인 조회

def get_pipeline_job(job_id: str) -> dict:
    """
    특정 파이프라인 작업 상세 조회
    
    Returns:
        {
            "job_id": str,
            "repo_url": str,
            "branch": str,
            "status": str,  # queued, running, success, failed, cancelled
            "trigger_source": str,
            "created_at": datetime,
            "started_at": datetime | None,
            "completed_at": datetime | None,
            "total_duration_secs": int | None,
            "overall_result": "success" | "failed",
            "step_count": int,
            "security_issues_count": int
        }
    """

def list_pipeline_jobs(
    filters: dict = None,  # status, branch, date_from, date_to
    limit: int = 50,
    offset: int = 0
) -> list[dict]:
    """
    파이프라인 작업 목록 조회 (필터링, 페이징 지원)
    """

#### Update - 파이프라인 상태 업데이트

def update_pipeline_job_status(
    job_id: str,
    status: str,  # "queued", "running", "completed", "failed", "cancelled"
    metadata: dict = None
) -> bool:
    """
    파이프라인 상태 업데이트 (Ubuntu에서 결과 수신 시 호출)
    """

---

### 1.2 파이프라인 Step 관리

#### Create - Step 기록

def record_step_execution(
    job_id: str,
    step_name: str,  # "clone", "install", "lightweight_security_scan", "test", "deep_security_scan", "build", "deploy"
    step_type: str,
    status: str,  # "success", "failed", "skipped"
    started_at: datetime,
    ended_at: datetime,
    error_message: str = None,
    metadata: dict = None
) -> dict:
    """
    파이프라인 각 단계 실행 결과 기록
    
    Returns:
        {
            "step_id": "660e8400-e29b-41d4-a716-446655440001",
            "job_id": str,
            "duration_secs": 210.5,
            "created_at": datetime
        }
    """

#### Read - Step 결과 조회

def get_step_details(job_id: str) -> list[dict]:
    """
    특정 job_id에 속한 모든 step 결과 조회 (시간순)
    
    Returns:
        [
            {
                "step_id": str,
                "step_name": str,
                "status": str,  # success, failed, skipped
                "duration_secs": float,
                "error_message": str | None,
                "log_count": int,
                "started_at": datetime,
                "ended_at": datetime
            },
            ...  # 7개 step
        ]
    """

---

### 1.3 로그 관리

#### Create - 로그 기록

def save_step_log(
    job_id: str,
    step_id: str,
    log_content: str,  # 100줄 단위 배치
    log_level: str = "info",  # "debug", "info", "warn", "error"
    timestamp: datetime = None
) -> bool:
    """
    Step 단계별 로그 저장 (배치로 저장)
    
    💡 최적화: 한 라인마다 INSERT ❌
            100줄 단위로 묶어서 저장 ✅
    """

#### Read - 로그 조회

def get_step_logs(
    job_id: str,
    step_id: str = None,
    log_level: str = None,
    limit: int = 10000
) -> list[dict]:
    """
    Step별 로그 조회 (원본 로그 검색 시 필요)
    
    Returns:
        [
            {
                "log_id": str,
                "timestamp": datetime,
                "log_level": str,
                "content": str  # 최대 5000자
            },
            ...
        ]
    """

---

### 1.4 보안 스캔 결과 관리

#### Create - 경량 스캔 (gitleaks)

def save_lightweight_security_scan(
    job_id: str,
    step_id: str,
    scan_type: str,  # "gitleaks"
    status: str,  # "success", "failed", "warning"
    finding_count: int,
    findings: list[dict] = None,
    metadata: dict = None
) -> bool:
    """
    gitleaks 경량 스캔 결과 저장 (시크릿 유출 감지)
    
    findings 구조:
        [
            {
                "type": "secret_leak",
                "severity": "high",  # low, medium, high, critical
                "file_path": str,
                "line_number": int,
                "description": str,
                "match": str,  # 마스킹됨 (원본 X)
                "rule_id": str  # slack-bot-token 등
            },
            ...
        ]
    """

#### Create - 심화 스캔 (semgrep)

def save_deep_security_scan(
    job_id: str,
    step_id: str,
    scan_type: str,  # "semgrep"
    status: str,  # "success", "failed"
    finding_count: int,
    critical_count: int,
    high_count: int,
    medium_count: int,
    low_count: int,
    findings: list[dict] = None,
    metadata: dict = None
) -> bool:
    """
    semgrep 심화 스캔 결과 저장 (코드 취약점 분석)
    
    findings 구조:
        [
            {
                "severity": "critical" | "high" | "medium" | "low",
                "rule_id": str,
                "rule_name": str,
                "file_path": str,
                "line_number": int,
                "column_number": int,
                "message": str,
                "code_snippet": str,
                "cwe_id": str,  # CWE-506 등
                "cvss_score": float  # 0.0~10.0
            },
            ...
        ]
    """

#### Read - 보안 이슈 조회

def get_security_findings(
    job_id: str = None,
    severity: str = None,  # "all", "critical", "high", "medium", "low"
    scan_type: str = None,  # "gitleaks", "semgrep", None(all)
    limit: int = 1000
) -> list[dict]:
    """
    보안 취약점 목록 조회
    
    Returns:
        [
            {
                "job_id": str,
                "step_id": str,
                "scan_type": str,
                "severity": str,
                "file_path": str,
                "line_number": int,
                "rule_id": str,
                "description": str,
                "created_at": datetime
            },
            ...
        ]
    """

#### Read - 보안 요약

def get_security_summary(job_id: str) -> dict:
    """
    Job 내 보안 스캔 결과 요약
    
    Returns:
        {
            "total_findings": int,
            "by_severity": {
                "critical": int,
                "high": int,
                "medium": int,
                "low": int
            },
            "by_scan_type": {
                "gitleaks": int,
                "semgrep": int
            },
            "overall_status": "passed" | "failed" | "warning",
            "blocking_issues": list[str]  # CVSS >= 9.0 등
        }
    """

---

### 1.5 빌드 아티팩트 관리

#### Create - 아티팩트 메타데이터 저장

def save_build_artifact(
    job_id: str,
    step_id: str,
    artifact_name: str,
    artifact_type: str,  # "jar", "docker", "wheel", "zip", etc.
    location: str,  # S3 URL or local path
    size_bytes: int,
    checksum: str = None,  # SHA256
    metadata: dict = None
) -> dict:
    """
    빌드 아티팩트 메타데이터 저장 (실제 파일은 S3에)
    
    Returns:
        {
            "artifact_id": str,
            "created_at": datetime
        }
    """

#### Read - 아티팩트 조회

def get_build_artifacts(job_id: str) -> list[dict]:
    """
    특정 job의 빌드 아티팩트 조회
    
    Returns:
        [
            {
                "artifact_id": str,
                "artifact_name": str,
                "artifact_type": str,
                "location": str,  # S3 경로
                "size_bytes": int,
                "checksum": str,  # SHA256
                "created_at": datetime
            },
            ...
        ]
    """

---

### 1.6 배포 기록 관리

#### Create - 배포 기록

def record_deployment(
    job_id: str,
    artifact_id: str,
    target_env: str,  # "staging", "production"
    deployed_by: str,  # username or "system"
    deployment_status: str,  # "success", "failed", "rollback"
    deployment_result: dict = None
) -> dict:
    """
    배포 이력 기록
    
    Returns:
        {
            "deployment_id": str,
            "created_at": datetime
        }
    """

#### Read - 배포 조회

def get_deployment_history(
    job_id: str = None,
    target_env: str = None,
    limit: int = 100,
    offset: int = 0
) -> list[dict]:
    """
    배포 이력 조회
    
    Returns:
        [
            {
                "deployment_id": str,
                "job_id": str,
                "artifact_id": str,
                "target_env": str,
                "status": str,
                "deployed_at": datetime,
                "deployed_by": str
            },
            ...
        ]
    """

---

### 1.7 분석 및 통계 기능

#### 파이프라인 성공률

def get_pipeline_success_rate(
    date_from: datetime = None,
    date_to: datetime = None,
    branch: str = None
) -> dict:
    """
    일정 기간 파이프라인 성공률 계산
    
    Returns:
        {
            "total_runs": int,
            "successful_runs": int,
            "failed_runs": int,
            "success_rate_percent": float,
            "avg_duration_secs": float
        }
    """

#### 가장 많이 발생하는 보안 이슈

def get_top_security_issues(
    limit: int = 20,
    days: int = 30
) -> list[dict]:
    """
    최근 30일간 가장 많이 나타나는 보안 이슈
    
    Returns:
        [
            {
                "rule_id": str,
                "severity": str,
                "occurrence_count": int,
                "file_paths": list[str],  # 상위 5개 파일
                "last_seen": datetime
            },
            ...
        ]
    """

#### Branch별 통계

def get_branch_statistics() -> list[dict]:
    """
    Branch별 파이프라인 실행 통계
    
    Returns:
        [
            {
                "branch": str,
                "total_runs": int,
                "success_count": int,
                "latest_run_at": datetime,
                "avg_duration_secs": float
            },
            ...
        ]
    """

---

## 📊 2부. DB 스키마 설계

### 2.1 테이블 정의

#### pipeline_jobs

CREATE TABLE pipeline_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_url VARCHAR(2048) NOT NULL,
    branch VARCHAR(255) NOT NULL DEFAULT 'main',
    trigger_source VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    overall_result VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_secs INTEGER,
    metadata JSONB DEFAULT '{}',
    
    CONSTRAINT chk_job_status CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
    CONSTRAINT chk_overall_result CHECK (overall_result IN ('success', 'failed') OR overall_result IS NULL)
);

Comment: 파이프라인 작업의 최상위 정보

#### pipeline_steps

CREATE TABLE pipeline_steps (
    step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_name VARCHAR(100) NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_secs FLOAT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_step_status CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
    CONSTRAINT chk_step_dates CHECK (started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at)
);

Comment: 각 파이프라인 단계의 실행 결과

#### step_logs

CREATE TABLE step_logs (
    log_id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    log_level VARCHAR(10) NOT NULL DEFAULT 'info',
    log_content TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_log_level CHECK (log_level IN ('debug', 'info', 'warn', 'error'))
);

Comment: 단계별 상세 로그 (배치 저장 권고)

저장 전략:
- 100줄씩 묶어서 INSERT
- 한 라인싱 저장 X

#### security_findings

CREATE TABLE security_findings (
    finding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    scan_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    rule_id VARCHAR(255) NOT NULL,
    rule_name VARCHAR(500),
    file_path VARCHAR(2048) NOT NULL,
    line_number INTEGER NOT NULL,
    column_number INTEGER,
    message TEXT NOT NULL,
    code_snippet TEXT,
    cwe_id VARCHAR(20),
    cvss_score FLOAT,
    is_masked BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_finding_severity CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    CONSTRAINT chk_scan_type CHECK (scan_type IN ('gitleaks', 'semgrep')),
    CONSTRAINT chk_cvss CHECK (cvss_score IS NULL OR (cvss_score >= 0.0 AND cvss_score <= 10.0))
);

Comment: gitleaks (시크릿) + semgrep (코드 취약점) 결과

#### build_artifacts

CREATE TABLE build_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    artifact_name VARCHAR(500) NOT NULL,
    artifact_type VARCHAR(50) NOT NULL,
    location VARCHAR(2048) NOT NULL,
    size_bytes BIGINT NOT NULL,
    checksum VARCHAR(128),
    checksum_algorithm VARCHAR(20) DEFAULT 'sha256',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_artifact_type CHECK (artifact_type IN ('jar', 'docker', 'wheel', 'zip', 'tar', 'tar.gz', 'other'))
);

Comment: 빌드 아티팩트 메타데이터 (파일은 S3에 저장)

#### deployments

CREATE TABLE deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES build_artifacts(artifact_id) ON DELETE CASCADE,
    target_env VARCHAR(50) NOT NULL,
    deployment_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    deployed_by VARCHAR(255),
    deployed_at TIMESTAMP WITH TIME ZONE,
    rolled_back_at TIMESTAMP WITH TIME ZONE,
    deployment_result JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_deploy_status CHECK (deployment_status IN ('pending', 'in_progress', 'success', 'failed', 'rollback')),
    CONSTRAINT chk_deploy_env CHECK (target_env IN ('dev', 'staging', 'production'))
);

Comment: 배포 이력 기록

#### security_summary
```sql
CREATE TABLE security_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL UNIQUE REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    total_findings INT DEFAULT 0,
    critical_count INT DEFAULT 0,
    high_count INT DEFAULT 0,
    medium_count INT DEFAULT 0,
    low_count INT DEFAULT 0,
    gitleaks_count INT DEFAULT 0,
    semgrep_count INT DEFAULT 0,
    overall_status VARCHAR(20) DEFAULT 'passed',
    status_reason TEXT,
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_summary_status CHECK (overall_status IN ('passed', 'warning', 'failed'))
);

Comment: security_findings 집계 (캐시 테이블)
Note: security_findings INSERT 시 트리거로 자동 업데이트
```

---

### 2.2 인덱스 전략

#### pipeline_jobs 인덱스

CREATE INDEX idx_pipeline_jobs_status ON pipeline_jobs(status);
CREATE INDEX idx_pipeline_jobs_created_at ON pipeline_jobs(created_at DESC);
CREATE INDEX idx_pipeline_jobs_branch ON pipeline_jobs(branch);
CREATE INDEX idx_pipeline_jobs_repo_url ON pipeline_jobs(repo_url);

#### pipeline_steps 인덱스

CREATE INDEX idx_pipeline_steps_job_id ON pipeline_steps(job_id);
CREATE INDEX idx_pipeline_steps_status ON pipeline_steps(status);
CREATE INDEX idx_pipeline_steps_job_created ON pipeline_steps(job_id, created_at DESC);

#### security_findings 인덱스

CREATE INDEX idx_security_findings_job_id ON security_findings(job_id);
CREATE INDEX idx_security_findings_severity ON security_findings(severity);
CREATE INDEX idx_security_findings_scan_type ON security_findings(scan_type);
CREATE INDEX idx_security_findings_job_severity ON security_findings(job_id, severity DESC);
CREATE INDEX idx_security_findings_created_at ON security_findings(created_at DESC);

#### step_logs 인덱스

CREATE INDEX idx_step_logs_job_id ON step_logs(job_id);
CREATE INDEX idx_step_logs_step_id ON step_logs(step_id);
CREATE INDEX idx_step_logs_job_timestamp ON step_logs(job_id, timestamp DESC);

#### build_artifacts 인덱스

CREATE INDEX idx_build_artifacts_job_id ON build_artifacts(job_id);
CREATE INDEX idx_build_artifacts_created_at ON build_artifacts(created_at DESC);

#### deployments 인덱스

CREATE INDEX idx_deployments_job_id ON deployments(job_id);
CREATE INDEX idx_deployments_artifact_id ON deployments(artifact_id);
CREATE INDEX idx_deployments_env_status ON deployments(target_env, deployment_status);
CREATE INDEX idx_deployments_deployed_at ON deployments(deployed_at DESC);

---

### 2.3 성능 목표

| 쿼리 유형 | 예상 응답시간 | 인덱스 |
|---------|-----------|--------|
| Job 조회 (limit 100) | < 100ms | created_at DESC |
| 보안 이슈 검색 (severity) | < 200ms | job_id, severity |
| 로그 조회 (1000줄) | < 500ms | step_id, timestamp |
| 월간 통계 | < 1s | date range |

---

### 2.4 데이터 무결성 규칙

- **job_id**: UUID v4, NOT NULL, UNIQUE, PK
- **step_id**: UUID v4, NOT NULL, UNIQUE, FK
- **상태 필드**: ENUM 타입 제약
- **외래키**: 모두 ON DELETE CASCADE
  - Job 삭제 → 모든 관련 데이터 자동 삭제

---

### 2.5 보안 제약

#### 저장 금지
- 실제 API 키/토큰 → gitleaks 자동 감지 + 마스킹
- 개인정보 → 로그 필터링으로 자동 제거

#### 마스킹 구현

발견: "slack-bot-token: xoxb-1234567890-abc123def456"
저장: "slack-bot-token: ***"
is_masked: true

---

### 2.6 샘플 데이터

#### pipeline_jobs 예시

{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "repo_url": "https://github.com/CapstoneLab/Capstone-Back.git",
    "branch": "develop",
    "trigger_source": "web-ui",
    "status": "success",
    "overall_result": "success",
    "created_at": "2026-04-09T10:00:00Z",
    "started_at": "2026-04-09T10:01:00Z",
    "completed_at": "2026-04-09T10:15:30Z",
    "duration_secs": 870,
    "metadata": {
        "requester_id": "user123",
        "commit_hash": "abc123def456"
    }
}

#### security_findings 예시 (gitleaks)

{
    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440002",
    "scan_type": "gitleaks",
    "severity": "critical",
    "rule_id": "slack-bot-token",
    "file_path": "config/.env",
    "line_number": 5,
    "message": "Slack Bot Token detected",
    "is_masked": true,
    "created_at": "2026-04-09T10:02:30Z"
}

#### security_findings 예시 (semgrep)

{
    "finding_id": "880e8400-e29b-41d4-a716-446655440003",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440004",
    "scan_type": "semgrep",
    "severity": "high",
    "rule_id": "use-of-assert",
    "rule_name": "Use of assert statement",
    "file_path": "app/service.py",
    "line_number": 150,
    "column_number": 8,
    "message": "Assertions are disabled in production",
    "code_snippet": "assert user_id is not None",
    "cwe_id": "CWE-506",
    "cvss_score": 5.3,
    "created_at": "2026-04-09T10:13:00Z"
}

---

### 2.7 데이터 보존 정책

| 테이블 | 보존 기간 | 아카이빙 |
|-------|---------|---------|
| pipeline_jobs | 1년 | S3 콜드 스토리지 |
| step_logs | 6개월 | 압축 후 아카이빙 |
| security_findings | 2년 | 컴플라이언스 보존 |
| build_artifacts | 3개월 | S3 (액세스 불가시 삭제) |
| deployments | 1년 | 감시 추적용 |

---

## 📈 저장 용량 추정

| 테이블 | 행/월 | 크기/월 | 비율 |
|-------|------|--------|------|
| pipeline_jobs | 1,000 | 500KB | 0.6% |
| pipeline_steps | 7,000 | 2.1MB | 2.7% |
| step_logs | 70,000 | 70MB | 90.9% |
| security_findings | 5,000 | 4MB | 5.2% |
| build_artifacts | 1,000 | 400KB | 0.5% |
| deployments | 500 | 150KB | 0.2% |
| security_summary | 1,000 | 100KB | 0.1% |
| **합계** | **84,500** | **~77MB** | **100%** |

⚠️ **주의**: step_logs가 90% 이상 차지 → 배치 저장 필수!

---

**최종 상태**: ✅ 스키마 설계 완료, schema.sql로 바로 구현 가능

---

## � 시각화 자료

### 💾 테이블 관계도 (ER Diagram)

pipeline_jobs (부모)
    ├── pipeline_steps (1:N)
    │   ├── step_logs (1:N)
    │   └── security_findings (1:N)
    ├── build_artifacts (1:N)
    ├── deployments (1:N)
    └── security_summary (1:1)

### 🔄 데이터 흐름도

1. CI 엔진 실행
   ↓
2. /api/pipelines/start → pipeline_jobs 생성 (job_id)
   ↓
3. 각 Step 실행
   ├─ /api/pipelines/{job_id}/steps → pipeline_steps 기록
   ├─ /api/pipelines/{job_id}/logs → step_logs 저장 (100줄 배치)
   └─ /api/pipelines/{job_id}/security-findings → security_findings 저장
   ↓
4. 빌드 완료
   └─ /api/artifacts → build_artifacts 저장
   ↓
5. 배포 시작
   └─ /api/deployments → deployments 기록
   ↓
6. /api/pipelines/{job_id} → 조회 (전체 통합 결과)

### 📈 저장 공간 분포 (월간)

step_logs:      70MB (90.9%)  ████████████████████████████████████
security_findings: 4MB (5.2%)   ███
pipeline_steps:  2.1MB (2.7%)  ██
기타:           0.9MB (1.2%)   █

합계: 77MB/월 → 약 924MB/년

### 🎯 성능 목표 vs 실제 구현

쿼리 타입          목표 시간    인덱스 전략
Job 조회           < 100ms     created_at DESC
보안 이슈 검색      < 200ms     (job_id, severity)
로그 조회           < 500ms     (step_id, timestamp)
월간 통계          < 1s        date range partition

### 🔐 보안 계층

┌─────────────────────────────────┐
│  gitleaks (비밀 감지)            │
│  - API 키, 토큰 자동 마스킹     │
│  - is_masked=true 플래그        │
└─────────────────────────────────┘
         ↓
┌─────────────────────────────────┐
│  semgrep (코드 취약점 분석)      │
│  - CVSS 점수 기록               │
│  - CWE ID 연계                  │
└─────────────────────────────────┘
         ↓
┌─────────────────────────────────┐
│  security_summary (자동 집계)    │
│  - 심각도별 카운팅              │
│  - 전체 상태 자동 판정          │
└─────────────────────────────────┘

### 📅 데이터 보존 정책

pipeline_jobs      |||||||||||||||||||||| 1년
step_logs          |||||||||| 6개월
security_findings  |||||||||||||||||||||||||||||| 2년
build_artifacts    |||||| 3개월
deployments        |||||||||||||||||||||| 1년

### 🗂️ 테이블별 용도

테이블            주요 역할           액세스 패턴
─────────────────────────────────────────────────
pipeline_jobs     Job 상태 관리       자주 조회
pipeline_steps    Step 실행 기록      Step별 조회
step_logs         상세 로그           검색/분석
security_findings 보안 이슈 저장      매우 자주
build_artifacts   빌드 메타           배포 시 조회
deployments       배포 이력           감시/감사
security_summary  보안 요약 캐시      대시보드

---

**마지막 업데이트**: 2026-04-09
**문서 버전**: v2.0-simplified
**상태**: ✅ DB 설계 완료, 팀 검토 준비됨
