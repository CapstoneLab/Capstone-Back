-- ========================================================================
-- CI/CD Pipeline Database Schema (Docker Init)
-- ========================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ========================================================================
-- ENUMS
-- ========================================================================

CREATE TYPE job_status AS ENUM ('queued', 'running', 'success', 'failed', 'cancelled');
CREATE TYPE step_status AS ENUM ('pending', 'running', 'success', 'failed', 'skipped');
CREATE TYPE severity_level AS ENUM ('critical', 'high', 'medium', 'low');
CREATE TYPE scan_type AS ENUM ('gitleaks', 'semgrep');
CREATE TYPE artifact_type AS ENUM ('jar', 'docker', 'wheel', 'zip', 'tar', 'tar.gz', 'other');
CREATE TYPE deployment_status AS ENUM ('pending', 'in_progress', 'success', 'failed', 'rollback');
CREATE TYPE environment AS ENUM ('dev', 'staging', 'production');

-- ========================================================================
-- TABLES
-- ========================================================================

CREATE TABLE pipeline_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_url VARCHAR(2048) NOT NULL,
    branch VARCHAR(255) NOT NULL DEFAULT 'main',
    trigger_source VARCHAR(50) NOT NULL,
    status job_status NOT NULL DEFAULT 'queued',
    overall_result VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_secs INTEGER,
    metadata JSONB DEFAULT '{}',
    CONSTRAINT chk_job_dates CHECK (started_at IS NULL OR started_at >= created_at),
    CONSTRAINT chk_job_completed CHECK (completed_at IS NULL OR completed_at >= created_at),
    CONSTRAINT chk_overall_result CHECK (overall_result IN ('success', 'failed') OR overall_result IS NULL)
);

CREATE INDEX idx_pipeline_jobs_status ON pipeline_jobs(status);
CREATE INDEX idx_pipeline_jobs_created_at ON pipeline_jobs(created_at DESC);
CREATE INDEX idx_pipeline_jobs_branch ON pipeline_jobs(branch);
CREATE INDEX idx_pipeline_jobs_repo_url ON pipeline_jobs(repo_url);

CREATE TABLE pipeline_steps (
    step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_name VARCHAR(100) NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    status step_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_secs FLOAT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_step_dates CHECK (started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX idx_pipeline_steps_job_id ON pipeline_steps(job_id);
CREATE INDEX idx_pipeline_steps_status ON pipeline_steps(status);
CREATE INDEX idx_pipeline_steps_job_created ON pipeline_steps(job_id, created_at DESC);

CREATE TABLE step_logs (
    log_id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    log_level VARCHAR(10) NOT NULL DEFAULT 'info',
    log_content TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_step_logs_job_id ON step_logs(job_id);
CREATE INDEX idx_step_logs_step_id ON step_logs(step_id);
CREATE INDEX idx_step_logs_job_timestamp ON step_logs(job_id, timestamp DESC);

CREATE TABLE security_findings (
    finding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    scan_type scan_type NOT NULL,
    severity severity_level NOT NULL,
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
    CONSTRAINT chk_cvss CHECK (cvss_score IS NULL OR (cvss_score >= 0.0 AND cvss_score <= 10.0))
);

CREATE INDEX idx_security_findings_job_id ON security_findings(job_id);
CREATE INDEX idx_security_findings_severity ON security_findings(severity);
CREATE INDEX idx_security_findings_scan_type ON security_findings(scan_type);
CREATE INDEX idx_security_findings_job_severity ON security_findings(job_id, severity DESC);
CREATE INDEX idx_security_findings_created_at ON security_findings(created_at DESC);

CREATE TABLE build_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES pipeline_steps(step_id) ON DELETE CASCADE,
    artifact_name VARCHAR(500) NOT NULL,
    artifact_type artifact_type NOT NULL,
    location VARCHAR(2048) NOT NULL,
    size_bytes BIGINT NOT NULL,
    checksum VARCHAR(128),
    checksum_algorithm VARCHAR(20) DEFAULT 'sha256',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_build_artifacts_job_id ON build_artifacts(job_id);
CREATE INDEX idx_build_artifacts_created_at ON build_artifacts(created_at DESC);

CREATE TABLE deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES build_artifacts(artifact_id) ON DELETE CASCADE,
    target_env environment NOT NULL,
    deployment_status deployment_status NOT NULL DEFAULT 'pending',
    deployed_by VARCHAR(255),
    deployed_at TIMESTAMP WITH TIME ZONE,
    rolled_back_at TIMESTAMP WITH TIME ZONE,
    deployment_result JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_deployments_job_id ON deployments(job_id);
CREATE INDEX idx_deployments_artifact_id ON deployments(artifact_id);
CREATE INDEX idx_deployments_env_status ON deployments(target_env, deployment_status);
CREATE INDEX idx_deployments_deployed_at ON deployments(deployed_at DESC);

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

CREATE INDEX idx_security_summary_job_id ON security_summary(job_id);

-- ========================================================================
-- SAMPLE DATA
-- ========================================================================

INSERT INTO pipeline_jobs (repo_url, branch, trigger_source, status, overall_result, started_at, completed_at, duration_secs)
VALUES (
    'https://github.com/CapstoneLab/Capstone-Back.git',
    'develop',
    'api',
    'success',
    'success',
    CURRENT_TIMESTAMP - INTERVAL '30 minutes',
    CURRENT_TIMESTAMP - INTERVAL '15 minutes',
    900
);

INSERT INTO pipeline_jobs (repo_url, branch, trigger_source, status, overall_result, started_at, completed_at, duration_secs)
VALUES (
    'https://github.com/juice-shop/juice-shop',
    'main',
    'web-ui',
    'failed',
    'failed',
    CURRENT_TIMESTAMP - INTERVAL '2 hours',
    CURRENT_TIMESTAMP - INTERVAL '1 hour 45 minutes',
    900
);

-- ========================================================================
-- VIEW
-- ========================================================================

CREATE OR REPLACE VIEW v_job_summary AS
SELECT
    j.job_id, j.repo_url, j.branch, j.status, j.overall_result,
    j.created_at, j.completed_at, j.duration_secs,
    COUNT(DISTINCT s.step_id) as step_count,
    COUNT(DISTINCT sf.finding_id) as finding_count,
    COUNT(DISTINCT CASE WHEN sf.severity = 'critical' THEN sf.finding_id END) as critical_count,
    COUNT(DISTINCT CASE WHEN sf.severity = 'high' THEN sf.finding_id END) as high_count
FROM pipeline_jobs j
LEFT JOIN pipeline_steps s ON j.job_id = s.job_id
LEFT JOIN security_findings sf ON j.job_id = sf.job_id
GROUP BY j.job_id, j.repo_url, j.branch, j.status, j.overall_result, j.created_at, j.completed_at, j.duration_secs;

-- ========================================================================
-- TRIGGER FUNCTION
-- ========================================================================

CREATE OR REPLACE FUNCTION update_security_summary()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO security_summary (job_id, total_findings, critical_count, high_count, medium_count, low_count, gitleaks_count, semgrep_count)
    SELECT
        NEW.job_id,
        COUNT(*)::INT,
        COUNT(*) FILTER (WHERE severity = 'critical')::INT,
        COUNT(*) FILTER (WHERE severity = 'high')::INT,
        COUNT(*) FILTER (WHERE severity = 'medium')::INT,
        COUNT(*) FILTER (WHERE severity = 'low')::INT,
        COUNT(*) FILTER (WHERE scan_type = 'gitleaks')::INT,
        COUNT(*) FILTER (WHERE scan_type = 'semgrep')::INT
    FROM security_findings
    WHERE job_id = NEW.job_id
    ON CONFLICT (job_id) DO UPDATE SET
        total_findings = EXCLUDED.total_findings,
        critical_count = EXCLUDED.critical_count,
        high_count = EXCLUDED.high_count,
        medium_count = EXCLUDED.medium_count,
        low_count = EXCLUDED.low_count,
        gitleaks_count = EXCLUDED.gitleaks_count,
        semgrep_count = EXCLUDED.semgrep_count,
        calculated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_security_summary
AFTER INSERT ON security_findings
FOR EACH ROW
EXECUTE FUNCTION update_security_summary();
