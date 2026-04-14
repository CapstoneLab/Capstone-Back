# CI/CD DB 스키마 설계

**작성일**: 2026-04-09  
**DB**: PostgreSQL 14+  
**문자 인코딩**: UTF-8

---

## 📋 목차

1. [개요](#개요)
2. [테이블 정의](#테이블-정의)
3. [인덱스 전략](#인덱스-전략)
4. [생성 스크립트](#생성-스크립트)
5. [데이터 타입 가이드](#데이터-타입-가이드)
6. [제약조건 정리](#제약조건-정리)

---

## 개요

### 주요 특징

- **UUID 기반 Primary Key**: 분산 시스템 대응
- **타임스탬프 관리**: 모든 주요 테이블에 `created_at`, `updated_at` 포함
- **ENUM 타입 활용**: 상태/심각도 같은 제한된 값은 ENUM으로 관리
- **JSONB 지원**: 동적 메타데이터는 JSONB로 유연하게 저장
- **외래키 무결성**: 모두 ON DELETE CASCADE

---

## 테이블 정의

### 1. `pipeline_jobs` - 파이프라인 작업

각 CI/CD 실행의 최상위 레벨 정보입니다.

```sql
CREATE TABLE pipeline_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_url VARCHAR(2048) NOT NULL,
    branch VARCHAR(255) NOT NULL DEFAULT 'main',
    trigger_source VARCHAR(50) NOT NULL,  -- "web-ui", "api", "scheduled"
    status VARCHAR(20) NOT NULL DEFAULT 'queued',  -- "queued", "running", "success", "failed", "cancelled"
    overall_result VARCHAR(20),  -- "success", "failed", null (진행 중)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    
    -- 계산 필드
    duration_secs INTEGER,  -- (completed_at - started_at) 초
    
    -- 메타데이터
    metadata JSONB DEFAULT '{}',  -- 추가 정보 저장용
    
    CONSTRAINT chk_job_status CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
    CONSTRAINT chk_overall_result CHECK (overall_result IN ('success', 'failed') OR overall_result IS NULL),
    CONSTRAINT chk_job_dates CHECK (started_at IS NULL OR started_at >= created_at),
    CONSTRAINT chk_job_completed CHECK (completed_at IS NULL OR completed_at >= created_at)
);

COMMENT ON TABLE pipeline_jobs IS 'CI/CD 파이프라인 작업 기록';
COMMENT ON COLUMN pipeline_jobs.job_id IS '고유 작업 ID (UUID)';
COMMENT ON COLUMN pipeline_jobs.status IS '현재 작업 상태';
COMMENT ON COLUMN pipeline_jobs.overall_result IS '최종 성공/실패 여부';
COMMENT ON COLUMN pipeline_jobs.metadata IS '확장 가능한 메타데이터 (JSON)';
```

**샘플 데이터**:
```json
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
```

---

### 2. `pipeline_steps` - 각 Step 실행 기록

파이프라인 내 각 단계(clone, install, test, build 등)의 실행 결과입니다.

```sql
CREATE TABLE pipeline_steps (
    step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_name VARCHAR(100) NOT NULL,  -- "clone", "install", "lightweight_security_scan", "test", "deep_security_scan", "build", "deploy"
    step_type VARCHAR(50) NOT NULL,  -- 향후 커스텀 step 지원용
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- "pending", "running", "success", "failed", "skipped"
    error_message TEXT,
    
    -- 시간 정보
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_secs FLOAT,  -- (ended_at - started_at) 초
    
    -- 메타데이터
    metadata JSONB DEFAULT '{}',
    
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_step_status CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
    CONSTRAINT chk_step_dates CHECK (started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at)
);

COMMENT ON TABLE pipeline_steps IS '파이프라인 각 단계의 실행 기록';
COMMENT ON COLUMN pipeline_steps.step_name IS '단계 이름 (clone, install, test 등)';
COMMENT ON COLUMN pipeline_steps.status IS '단계 실행 상태';
COMMENT ON COLUMN pipeline_steps.duration_secs IS '단계 소요 시간 (초)';
```

**샘플 데이터**:
```json
{
    "step_id": "660e8400-e29b-41d4-a716-446655440001",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_name": "test",
    "step_type": "test",
    "status": "success",
    "error_message": null,
    "started_at": "2026-04-09T10:05:00Z",
    "ended_at": "2026-04-09T10:08:30Z",
    "duration_secs": 210.5,
    "metadata": {
        "test_framework": "pytest",
        "test_count": 45,
        "passed": 45,
        "failed": 0
    }
}
```

---

### 3. `step_logs` - 단계별 로그

각 Step 실행 중 생성되는 상세 로그입니다.

```sql
CREATE TABLE step_logs (
    log_id BIGSERIAL PRIMARY KEY,  -- 빠른 조회를 위해 BIGSERIAL 사용
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    
    log_level VARCHAR(10) NOT NULL DEFAULT 'info',  -- "debug", "info", "warn", "error"
    log_content TEXT NOT NULL,  -- 최대 5000자 per line (또는 배치)
    
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_log_level CHECK (log_level IN ('debug', 'info', 'warn', 'error'))
);

COMMENT ON TABLE step_logs IS '파이프라인 Step 실행 로그';
COMMENT ON COLUMN step_logs.log_content IS '로그 내용 (민감정보 마스킹됨)';
```

**저장 전략**:
- 로그는 Step 완료 후 **배치로 저장** (매 라인마다 INSERT ❌)
- 한 배치: 최대 5000자 or 100줄
- 예: 1000줄의 로그 → 10~50건의 INSERT

**샘플 데이터**:
```
log_id: 1001
step_id: 660e8400-e29b-41d4-a716-446655440001
log_level: info
log_content: "Dependencies installed: 23 packages"
timestamp: 2026-04-09T10:05:15Z
```

---

### 4. `security_findings` - 보안 스캔 결과

gitleaks 및 semgrep의 취약점 발견 정보입니다.

```sql
CREATE TABLE security_findings (
    finding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    
    -- 스캔 타입
    scan_type VARCHAR(50) NOT NULL,  -- "gitleaks", "semgrep"
    severity VARCHAR(20) NOT NULL,  -- "critical", "high", "medium", "low"
    
    -- 취약점 정보
    rule_id VARCHAR(255) NOT NULL,  -- gitleaks rule, semgrep rule ID
    rule_name VARCHAR(500),
    
    -- 파일 위치
    file_path VARCHAR(2048) NOT NULL,
    line_number INTEGER NOT NULL,
    column_number INTEGER,
    
    -- 상세 정보
    message TEXT NOT NULL,
    code_snippet TEXT,  -- 취약점 관련 코드 라인
    
    -- 추가 정보
    cwe_id VARCHAR(20),  -- CWE-XXX format
    cvss_score FLOAT,  -- 0.0 ~ 10.0, CVSS 3.1 기준
    
    -- 시큐리티 정보
    is_masked BOOLEAN DEFAULT FALSE,  -- 민감정보 마스킹 여부
    metadata JSONB DEFAULT '{}',
    
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_finding_severity CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    CONSTRAINT chk_scan_type CHECK (scan_type IN ('gitleaks', 'semgrep')),
    CONSTRAINT chk_cvss CHECK (cvss_score IS NULL OR (cvss_score >= 0.0 AND cvss_score <= 10.0))
);

COMMENT ON TABLE security_findings IS '보안 스캔 결과 (gitleaks, semgrep)';
COMMENT ON COLUMN security_findings.severity IS '취약점 심각도';
COMMENT ON COLUMN security_findings.rule_id IS '취약점 규칙 ID';
COMMENT ON COLUMN security_findings.is_masked IS '민감정보 마스킹 여부';
```

**샘플 데이터 (gitleaks)**:
```json
{
    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440002",
    "scan_type": "gitleaks",
    "severity": "critical",
    "rule_id": "slack-bot-token",
    "file_path": "config/secrets.env",
    "line_number": 5,
    "message": "Slack Bot Token detected",
    "is_masked": true,
    "created_at": "2026-04-09T10:02:30Z"
}
```

**샘플 데이터 (semgrep)**:
```json
{
    "finding_id": "880e8400-e29b-41d4-a716-446655440003",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440004",
    "scan_type": "semgrep",
    "severity": "high",
    "rule_id": "python.lang.best-practice.use-of-assert",
    "rule_name": "Use of assert statement",
    "file_path": "app/service.py",
    "line_number": 150,
    "column_number": 8,
    "message": "Assertions are disabled in production code (python -O)",
    "code_snippet": "assert user_id is not None",
    "cwe_id": "CWE-506",
    "cvss_score": 5.3,
    "created_at": "2026-04-09T10:13:00Z"
}
```

---

### 5. `build_artifacts` - 빌드 아티팩트

빌드 결과물의 메타데이터입니다.

```sql
CREATE TABLE build_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    
    -- 아티팩트 정보
    artifact_name VARCHAR(500) NOT NULL,
    artifact_type VARCHAR(50) NOT NULL,  -- "jar", "docker", "wheel", "zip", "tar", etc.
    
    -- 저장 위치
    location VARCHAR(2048) NOT NULL,  -- S3 URL 또는 로컬 경로
    size_bytes BIGINT NOT NULL,
    
    -- 무결성 검증
    checksum VARCHAR(128),  -- SHA256 hex string
    checksum_algorithm VARCHAR(20) DEFAULT 'sha256',  -- "md5", "sha256"
    
    -- 메타데이터
    metadata JSONB DEFAULT '{}',
    
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_artifact_type CHECK (artifact_type IN ('jar', 'docker', 'wheel', 'zip', 'tar', 'tar.gz', 'other'))
);

COMMENT ON TABLE build_artifacts IS '빌드 아티팩트 메타데이터';
COMMENT ON COLUMN build_artifacts.location IS 'S3 또는 로컬 저장소 경로';
COMMENT ON COLUMN build_artifacts.checksum IS 'SHA256 체크섬 (무결성 검증용)';
```

**샘플 데이터**:
```json
{
    "artifact_id": "990e8400-e29b-41d4-a716-446655440004",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_id": "660e8400-e29b-41d4-a716-446655440005",
    "artifact_name": "capstone-backend-0.1.0.jar",
    "artifact_type": "jar",
    "location": "s3://capstone-builds/2026-04-09/capstone-backend-0.1.0.jar",
    "size_bytes": 45678901,
    "checksum": "abc123def456...",
    "created_at": "2026-04-09T10:14:00Z",
    "metadata": {
        "java_version": "11",
        "spring_boot_version": "3.0.0"
    }
}
```

---

### 6. `deployments` - 배포 이력

빌드 후 배포 단계의 기록입니다.

```sql
CREATE TABLE deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES build_artifacts(artifact_id) ON DELETE CASCADE,
    
    -- 배포 정보
    target_env VARCHAR(50) NOT NULL,  -- "staging", "production", "dev"
    deployment_status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- "pending", "in_progress", "success", "failed", "rollback"
    
    -- 배포자 정보
    deployed_by VARCHAR(255),  -- username or "system"
    
    -- 배포 시간
    deployed_at TIMESTAMP WITH TIME ZONE,
    rolled_back_at TIMESTAMP WITH TIME ZONE,
    
    -- 배포 결과
    deployment_result JSONB DEFAULT '{}',  -- 배포 로그, 메시지 등
    
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_deploy_status CHECK (deployment_status IN ('pending', 'in_progress', 'success', 'failed', 'rollback')),
    CONSTRAINT chk_deploy_env CHECK (target_env IN ('dev', 'staging', 'production'))
);

COMMENT ON TABLE deployments IS 'CI/CD 배포 이력';
COMMENT ON COLUMN deployments.deployment_status IS '배포 상태';
COMMENT ON COLUMN deployments.deployment_result IS '배포 결과 상세 정보 (JSON)';
```

**샘플 데이터**:
```json
{
    "deployment_id": "aa0e8400-e29b-41d4-a716-446655440005",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "artifact_id": "990e8400-e29b-41d4-a716-446655440004",
    "target_env": "staging",
    "deployment_status": "success",
    "deployed_by": "cicd-system",
    "deployed_at": "2026-04-09T10:20:00Z",
    "created_at": "2026-04-09T10:15:00Z",
    "deployment_result": {
        "instance_ids": ["i-12345", "i-12346"],
        "health_check_passed": true,
        "response_time_ms": 150
    }
}
```

---

### 7. `security_summary` - 보안 요약 (캐시 테이블)

성능 최적화를 위해 Job별 보안 스캔 결과를 미리 집계합니다.

```sql
CREATE TABLE security_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL UNIQUE REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    
    -- 전체 집계
    total_findings INT DEFAULT 0,
    
    -- 심각도별 카운트
    critical_count INT DEFAULT 0,
    high_count INT DEFAULT 0,
    medium_count INT DEFAULT 0,
    low_count INT DEFAULT 0,
    
    -- 스캔 타입별 카운트
    gitleaks_count INT DEFAULT 0,
    semgrep_count INT DEFAULT 0,
    
    -- 최종 판정
    overall_status VARCHAR(20) DEFAULT 'passed',  -- "passed", "warning", "failed"
    status_reason TEXT,  -- 차단 사유 (CVSS >= 9.0 등)
    
    -- 타임스탬프
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_summary_status CHECK (overall_status IN ('passed', 'warning', 'failed'))
);

COMMENT ON TABLE security_summary IS '보안 스캔 결과 요약 (캐시)';
COMMENT ON COLUMN security_summary.overall_status IS 'Pass/Fail 판정';
```

---

## 인덱스 전략

### 주요 인덱스

```sql
-- 1. pipeline_jobs 인덱스
CREATE INDEX idx_pipeline_jobs_status ON pipeline_jobs(status);
CREATE INDEX idx_pipeline_jobs_created_at ON pipeline_jobs(created_at DESC);
CREATE INDEX idx_pipeline_jobs_branch ON pipeline_jobs(branch);
CREATE INDEX idx_pipeline_jobs_repo_url ON pipeline_jobs(repo_url);

-- 2. pipeline_steps 인덱스
CREATE INDEX idx_pipeline_steps_job_id ON pipeline_steps(job_id);
CREATE INDEX idx_pipeline_steps_status ON pipeline_steps(status);
CREATE INDEX idx_pipeline_steps_job_created ON pipeline_steps(job_id, created_at DESC);

-- 3. security_findings 인덱스
CREATE INDEX idx_security_findings_job_id ON security_findings(job_id);
CREATE INDEX idx_security_findings_severity ON security_findings(severity);
CREATE INDEX idx_security_findings_scan_type ON security_findings(scan_type);
CREATE INDEX idx_security_findings_job_severity ON security_findings(job_id, severity DESC);
CREATE INDEX idx_security_findings_created_at ON security_findings(created_at DESC);

-- 4. step_logs 인덱스
CREATE INDEX idx_step_logs_job_id ON step_logs(job_id);
CREATE INDEX idx_step_logs_step_id ON step_logs(step_id);
CREATE INDEX idx_step_logs_job_timestamp ON step_logs(job_id, timestamp DESC);

-- 5. build_artifacts 인덱스
CREATE INDEX idx_build_artifacts_job_id ON build_artifacts(job_id);
CREATE INDEX idx_build_artifacts_created_at ON build_artifacts(created_at DESC);

-- 6. deployments 인덱스
CREATE INDEX idx_deployments_job_id ON deployments(job_id);
CREATE INDEX idx_deployments_artifact_id ON deployments(artifact_id);
CREATE INDEX idx_deployments_env_status ON deployments(target_env, deployment_status);
CREATE INDEX idx_deployments_deployed_at ON deployments(deployed_at DESC);
```

---

## 생성 스크립트

### 전체 DB 생성

```sql
-- 데이터베이스 생성
CREATE DATABASE cicd_engine
    ENCODING 'UTF8'
    TEMPLATE template0
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE 'en_US.UTF-8';

-- 연결
\c cicd_engine

-- 확장 프로그램
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 테이블 생성 (위의 모든 CREATE TABLE 문 실행)
-- ... (위의 모든 CREATE TABLE 스크립트 복사)

-- 인덱스 생성 (위의 모든 INDEX 스크립트 실행)
-- ... (위의 모든 CREATE INDEX 스크립트 복사)

-- 초기 데이터
INSERT INTO pipeline_jobs (repo_url, branch, trigger_source, status, overall_result)
VALUES (
    'https://github.com/example/example.git',
    'main',
    'api',
    'success',
    'success'
);
```

### 실행 방법 (Ubuntu/Linux)

```bash
# PostgreSQL 클라이언트로 스크립트 실행
psql -U postgres -h localhost < schema.sql

# 또는 직접 SQL 파일 실행
psql -U postgres -h localhost -f schema.sql
```

---

## 데이터 타입 가이드

| 필드 타입 | PostgreSQL 타입 | 용도 | 범위/제약 |
|----------|-----------------|------|---------|
| ID | UUID | 주키, FK | 128비트 고유값 |
| 상태 | VARCHAR(20) | 제한된 값 | "success", "failed" 등 |
| URL | VARCHAR(2048) | 저장소/아티팩트 경로 | 최대 2048자 |
| 로그/메시지 | TEXT | 무제한 텍스트 | 용량에 따라 별도 정리 |
| 타임스탬프 | TIMESTAMP WITH TIME ZONE | 시간 기록 | UTC 기준 |
| 수치 | INTEGER / BIGINT / FLOAT | 카운트/크기/점수 | 범위에 따라 선택 |
| 메타데이터 | JSONB | 유연한 저장 | 인덱싱 가능 |

---

## 제약조건 정리

### 데이터 무결성

| 제약 조건 | 설명 | 적용 테이블 |
|----------|------|-----------|
| PRIMARY KEY | 고유성 및 NOT NULL | 모든 테이블 |
| FOREIGN KEY | 참조 무결성 | job_id, step_id FK |
| CHECK | 값 범위 검증 | status, severity 등 |
| UNIQUE | 고유성 | (필요시) job_id |
| NOT NULL | NULL 금지 | 필수 필드 |
| DEFAULT | 기본값 | created_at, status |

### ON DELETE CASCADE

모든 FK는 ON DELETE CASCADE로 설정하여, Job 삭제 시 관련 모든 데이터(Step, Log, Findings 등)가 자동 삭제됩니다.

```sql
step_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
```

---

## 성능 최적화 팁

### 1. 로그 저장 배치화

```python
# ❌ 나쁜 예: 매 라인마다 INSERT
for line in logs:
    db.insert('step_logs', ...)  # 10,000번 INSERT

# ✅ 좋은 예: 배치로 저장
accumulated = ""
for i, line in enumerate(logs):
    accumulated += line + "\n"
    if i % 100 == 0 or i == len(logs) - 1:
        db.insert('step_logs', log_content=accumulated)
        accumulated = ""
```

### 2. 보안 요약 캐싱

```sql
-- Job 완료 시 한 번만 계산
INSERT INTO security_summary (job_id, critical_count, high_count, ...)
SELECT 
    job_id,
    COUNT(*) FILTER (WHERE severity = 'critical'),
    COUNT(*) FILTER (WHERE severity = 'high'),
    ...
FROM security_findings
WHERE job_id = $1
GROUP BY job_id;
```

### 3. 파티셔닝 (대규모 데이터)

```sql
-- 월별 파티셔닝 (선택사항)
CREATE TABLE pipeline_jobs_2026_04 PARTITION OF pipeline_jobs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
```

---

## 마이그레이션 전략

### Phase 1: 초기 생성
1. 위 스크립트로 DB 초기 생성
2. 테스트 데이터 삽입

### Phase 2: 기존 시스템 연결
1. `app/main.py`의 `ResultStore` → PostgreSQL로 변경
2. `app/service.py`의 CRUD 함수 구현

### Phase 3: 최적화
1. 필요한 인덱스 추가
2. 쿼리 성능 테스트
3. 파티셔닝 적용 (필요시)

---

**End of Document**
