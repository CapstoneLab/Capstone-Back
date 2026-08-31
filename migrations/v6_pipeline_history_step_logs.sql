-- v6: 사용자별 파이프라인 실행 이력 + step 종료 시점 로그 저장

-- pipeline_jobs 한 행이 한 번의 파이프라인 실행 이력이다. 아래 컬럼은
-- 목록 화면의 진행 상태를 빠르게 반환하기 위한 스냅샷이다.
ALTER TABLE pipeline_jobs
    ADD COLUMN IF NOT EXISTS latest_step_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS completed_steps INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_steps INTEGER,
    ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ;

-- step callback으로 받은 순서와 로그 수신 상태를 기록한다.
ALTER TABLE pipeline_steps
    ADD COLUMN IF NOT EXISTS step_order INTEGER,
    ADD COLUMN IF NOT EXISTS log_line_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS log_size_bytes BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS callback_received_at TIMESTAMPTZ;

-- step별 로그 본문은 기존 step_logs를 사용한다. delivery_id/hash는 같은
-- callback이 재시도되어도 애플리케이션이 한 행으로 갱신할 수 있게 한다.
ALTER TABLE step_logs
    ADD COLUMN IF NOT EXISTS delivery_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS line_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS size_bytes BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'pipeline_complete';

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_user_created_at
    ON pipeline_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_user_status_created_at
    ON pipeline_jobs(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_steps_job_order
    ON pipeline_steps(job_id, step_order, created_at);
CREATE INDEX IF NOT EXISTS idx_step_logs_step_id
    ON step_logs(step_id);
CREATE INDEX IF NOT EXISTS idx_step_logs_delivery_id
    ON step_logs(delivery_id)
    WHERE delivery_id IS NOT NULL;

-- 기존 실행도 히스토리 목록에서 올바른 진행률과 최근 시간을 보이도록 보정한다.
UPDATE pipeline_jobs AS job
SET
    completed_steps = stats.completed_steps,
    total_steps = stats.total_steps,
    latest_step_name = COALESCE(job.latest_step_name, stats.latest_step_name),
    last_event_at = COALESCE(job.last_event_at, job.completed_at, stats.latest_step_at, job.started_at, job.created_at)
FROM (
    SELECT
        job_id,
        COUNT(*) FILTER (WHERE status IN ('success', 'failed', 'skipped'))::INTEGER AS completed_steps,
        COUNT(*)::INTEGER AS total_steps,
        (ARRAY_AGG(step_name ORDER BY COALESCE(ended_at, started_at, created_at) DESC))[1] AS latest_step_name,
        MAX(COALESCE(ended_at, started_at, created_at)) AS latest_step_at
    FROM pipeline_steps
    GROUP BY job_id
) AS stats
WHERE job.job_id = stats.job_id;
