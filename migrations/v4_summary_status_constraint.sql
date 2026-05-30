-- v4: security_summary overall_status constraint 확장
-- 엔진 verdict 값(block, warn, block_pending_approval, pass) 허용

ALTER TABLE security_summary DROP CONSTRAINT IF EXISTS chk_summary_status;
ALTER TABLE security_summary ADD CONSTRAINT chk_summary_status
    CHECK (overall_status = ANY (ARRAY[
        'passed', 'warning', 'failed',
        'block', 'warn', 'block_pending_approval', 'pass'
    ]));
