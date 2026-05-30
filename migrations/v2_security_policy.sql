-- v2: 보안 정책 16개 항목 연동 마이그레이션

-- 1) pipeline_jobs: selected_items, commit_sha 추가
ALTER TABLE pipeline_jobs
    ADD COLUMN IF NOT EXISTS selected_items JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS commit_sha VARCHAR(64);

-- 2) security_findings: cwe_id(이미 있음), policy_item, in_scope 추가
ALTER TABLE security_findings
    ADD COLUMN IF NOT EXISTS policy_item VARCHAR(100),
    ADD COLUMN IF NOT EXISTS in_scope BOOLEAN DEFAULT TRUE;

-- 3) security_summary: verdict 스냅샷 필드 추가
ALTER TABLE security_summary
    ADD COLUMN IF NOT EXISTS verdict VARCHAR(50),
    ADD COLUMN IF NOT EXISTS score FLOAT,
    ADD COLUMN IF NOT EXISTS score_label VARCHAR(200),
    ADD COLUMN IF NOT EXISTS gauge_color VARCHAR(20),
    ADD COLUMN IF NOT EXISTS selected_items JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS selected_count INTEGER,
    ADD COLUMN IF NOT EXISTS out_of_scope_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS block_reasons JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS warn_reasons JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS score_breakdown JSONB DEFAULT '{}';

-- 4) approval_records 테이블 신규 생성
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'approval_status') THEN
        CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS approval_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE,
    commit_sha VARCHAR(64),
    repo VARCHAR(2048) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    target_cwes JSONB NOT NULL DEFAULT '[]',
    block_reasons JSONB NOT NULL DEFAULT '[]',
    verdict_snapshot JSONB NOT NULL DEFAULT '{}',
    reason TEXT,
    approver_id VARCHAR(255),
    status approval_status NOT NULL DEFAULT 'pending',
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_approval_records_job_id ON approval_records(job_id);
CREATE INDEX IF NOT EXISTS idx_approval_records_status ON approval_records(status);
CREATE INDEX IF NOT EXISTS idx_approval_records_commit_sha ON approval_records(commit_sha);
