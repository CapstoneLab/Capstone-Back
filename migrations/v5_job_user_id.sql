-- v5: pipeline_jobs에 user_id 컬럼 추가 (repo_token 조회용)
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS user_id BIGINT;
