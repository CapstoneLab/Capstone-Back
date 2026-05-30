-- v3: commit_sha 검증 + approved_cwes 후속 잡 enqueue 지원

-- 1) pipeline_jobs: approved_cwes, approval_record_id 추가
ALTER TABLE pipeline_jobs
    ADD COLUMN IF NOT EXISTS approved_cwes JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS approval_record_id UUID;

-- 2) security_summary: scanned_commit_sha, acknowledged_cwes 추가
ALTER TABLE security_summary
    ADD COLUMN IF NOT EXISTS scanned_commit_sha VARCHAR(64),
    ADD COLUMN IF NOT EXISTS acknowledged_cwes JSONB DEFAULT '[]';

-- 3) approval_records: scanned_commit_sha, acknowledged_cwes, followup_job_id 추가
ALTER TABLE approval_records
    ADD COLUMN IF NOT EXISTS scanned_commit_sha VARCHAR(64),
    ADD COLUMN IF NOT EXISTS acknowledged_cwes JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS followup_job_id UUID;
