# CI/CD 보안 분석 플랫폼 DB 스키마 (UI 기반 재설계)

**작성일**: 2026-04-11
**대상 DB**: PostgreSQL 15+
**작성 기준**: 실제 앱 UI 3개 화면 (대시보드 / 파이프라인 진행 / 보안 분석 결과)

---

## 📋 목차

1. [화면 ↔ 테이블 매핑](#화면--테이블-매핑)
2. [ER 다이어그램](#er-다이어그램)
3. [테이블 정의 & 예시](#테이블-정의--예시)
   - [3.1 users](#31-users)
   - [3.2 repositories](#32-repositories)
   - [3.3 pipeline_runs](#33-pipeline_runs)
   - [3.4 pipeline_steps](#34-pipeline_steps)
   - [3.5 security_scans](#35-security_scans)
   - [3.6 security_findings](#36-security_findings)
   - [3.7 ai_suggestions](#37-ai_suggestions)
   - [3.8 platform_stats (뷰)](#38-platform_stats-뷰)
4. [화면별 조회 쿼리](#화면별-조회-쿼리)

---

## 화면 ↔ 테이블 매핑

| UI 요소 | 데이터 소스 |
|---|---|
| **화면 1 — 메인 대시보드** | |
| 분석된 프로젝트 (12,345) | `SELECT COUNT(*) FROM repositories` |
| 탐지된 취약점 (8,000+) | `SELECT COUNT(*) FROM security_findings` |
| 평균 분석 시간 (3m 47s) | `AVG(finished_at - started_at) FROM pipeline_runs` |
| **화면 2 — 파이프라인 진행** | |
| `myuser/web-app` | `repositories.full_name` |
| 브랜치 `main(default)` | `pipeline_runs.branch` + `repositories.default_branch` |
| `Run #643033` | `pipeline_runs.run_number` |
| `2m 23s` | `pipeline_runs.duration_seconds` |
| `7/7 파이프라인 통과` | `pipeline_runs.steps_passed / steps_total` |
| 7단계 진행바 | `pipeline_steps` (step_order 1-7) |
| **화면 3 — 보안 분석 결과** | |
| 스캔 ID | `security_scans.scan_uuid` |
| 파이프라인 실패/통과 | `security_scans.pipeline_result` |
| 보안 점수 62 / 쓰레스홀드 75 | `security_scans.security_score` / `threshold` |
| Critical/High/Medium/Low 카운트 | `security_scans.critical_count` 외 |
| 탐지된 취약점 목록 (CVE, CVSS, 경로, 설명) | `security_findings` |
| AI 제안 | `ai_suggestions` |

---

## ER 다이어그램

```
users (1)
  └─(N) repositories (1)
          └─(N) pipeline_runs (1)
                  ├─(N) pipeline_steps        [7단계]
                  └─(1) security_scans (1)
                          └─(N) security_findings (1)
                                  └─(1) ai_suggestions
```

---

## 테이블 정의 & 예시

### 3.1 users

GitHub 계정 정보. 모든 레포지토리의 소유자.

```sql
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    github_login    VARCHAR(100) NOT NULL UNIQUE,
    display_name    VARCHAR(150),
    avatar_url      VARCHAR(500),
    email           VARCHAR(200),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_login ON users(github_login);

COMMENT ON TABLE users IS 'GitHub 사용자/조직';
COMMENT ON COLUMN users.github_login IS 'GitHub 로그인 핸들 (예: myuser)';
```

**예시**

```sql
-- 사용자 등록
INSERT INTO users (github_login, display_name, avatar_url, email)
VALUES ('myuser', 'My User', 'https://github.com/myuser.png', 'myuser@example.com')
RETURNING id;

-- 조회
SELECT id, github_login, display_name
FROM users
WHERE github_login = 'myuser';
```

---

### 3.2 repositories

분석 대상 레포지토리. **화면 1의 "분석된 프로젝트 12,345"** 카운트 대상.

```sql
CREATE TABLE repositories (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repo_name       VARCHAR(200) NOT NULL,                    -- "web-app"
    full_name       VARCHAR(300) NOT NULL UNIQUE,             -- "myuser/web-app"
    repo_url        VARCHAR(500) NOT NULL,
    default_branch  VARCHAR(100) NOT NULL DEFAULT 'main',
    language        VARCHAR(50),                              -- "PHP", "Python"...
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, repo_name)
);

CREATE INDEX idx_repos_user ON repositories(user_id);
CREATE INDEX idx_repos_full_name ON repositories(full_name);
CREATE INDEX idx_repos_active ON repositories(is_active) WHERE is_active = TRUE;

COMMENT ON TABLE repositories IS '등록된 분석 대상 레포지토리';
```

**예시**

```sql
-- 등록
INSERT INTO repositories (user_id, repo_name, full_name, repo_url, default_branch, language)
VALUES (1, 'web-app', 'myuser/web-app', 'https://github.com/myuser/web-app', 'main', 'PHP')
RETURNING id;

-- 화면 1: 분석된 프로젝트 수
SELECT COUNT(*) AS analyzed_projects
FROM repositories
WHERE is_active = TRUE;
```

---

### 3.3 pipeline_runs

한 번의 파이프라인 실행. **화면 2 헤더 (`Run #643033`, `2m 23s`, `7/7 통과`)** 의 원천.

```sql
CREATE TABLE pipeline_runs (
    id                  BIGSERIAL PRIMARY KEY,
    repository_id       BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    run_number          INTEGER NOT NULL,                         -- "643033"
    run_uuid            UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    branch              VARCHAR(100) NOT NULL,                    -- "main"
    commit_sha          VARCHAR(40),
    trigger_type        VARCHAR(30) NOT NULL DEFAULT 'push',      -- push/manual/schedule
    status              VARCHAR(20) NOT NULL DEFAULT 'queued',    -- queued/running/success/failed/cancelled
    steps_total         SMALLINT NOT NULL DEFAULT 7,
    steps_passed        SMALLINT NOT NULL DEFAULT 0,
    duration_seconds    INTEGER,                                  -- 143 (= 2m 23s)
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (repository_id, run_number),
    CONSTRAINT chk_run_status CHECK (status IN ('queued','running','success','failed','cancelled'))
);

CREATE INDEX idx_runs_repo_created ON pipeline_runs(repository_id, created_at DESC);
CREATE INDEX idx_runs_status ON pipeline_runs(status);
CREATE INDEX idx_runs_uuid ON pipeline_runs(run_uuid);

COMMENT ON TABLE pipeline_runs IS '파이프라인 1회 실행 단위';
COMMENT ON COLUMN pipeline_runs.run_number IS '레포 단위 순번 (UI 표시용: Run #643033)';
```

**예시**

```sql
-- 실행 시작
INSERT INTO pipeline_runs (repository_id, run_number, branch, commit_sha, status, started_at)
VALUES (1, 643033, 'main', 'a1b2c3d4e5f6...', 'running', NOW())
RETURNING id, run_uuid;

-- 실행 종료 (화면 2 헤더 값 갱신)
UPDATE pipeline_runs
SET status           = 'success',
    steps_passed     = 7,
    duration_seconds = 143,
    finished_at      = NOW()
WHERE id = :run_id;

-- 화면 1: 평균 분석 시간
SELECT
    AVG(duration_seconds)::INT AS avg_seconds,
    (AVG(duration_seconds) / 60)::INT || 'm ' ||
    (AVG(duration_seconds)::INT % 60) || 's' AS avg_display
FROM pipeline_runs
WHERE status = 'success'
  AND finished_at >= NOW() - INTERVAL '30 days';
```

---

### 3.4 pipeline_steps

**화면 2의 7단계 진행 항목.** 고정된 7개 단계.

```sql
CREATE TABLE pipeline_steps (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_run_id     BIGINT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    step_order          SMALLINT NOT NULL,                        -- 1~7
    step_key            VARCHAR(40) NOT NULL,                     -- clone/install/light_scan/test/deep_scan/build/deploy
    step_name_ko        VARCHAR(50) NOT NULL,                     -- "레포지토리 클론"
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',   -- pending/running/success/failed/skipped
    duration_seconds    INTEGER,                                  -- 10, 20, 14...
    error_message       TEXT,
    log_excerpt         TEXT,                                     -- 확장 시 마지막 로그 일부
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,

    UNIQUE (pipeline_run_id, step_order),
    CONSTRAINT chk_step_order CHECK (step_order BETWEEN 1 AND 7),
    CONSTRAINT chk_step_status CHECK (status IN ('pending','running','success','failed','skipped'))
);

CREATE INDEX idx_steps_run ON pipeline_steps(pipeline_run_id, step_order);

COMMENT ON TABLE pipeline_steps IS '파이프라인 7단계 (화면 2 진행 과정)';
COMMENT ON COLUMN pipeline_steps.step_key IS 'clone/install/light_scan/test/deep_scan/build/deploy';
```

**예시**

```sql
-- 파이프라인 시작 시 7단계 일괄 생성
INSERT INTO pipeline_steps (pipeline_run_id, step_order, step_key, step_name_ko) VALUES
    (:run_id, 1, 'clone',       '레포지토리 클론'),
    (:run_id, 2, 'install',     '의존성 설치'),
    (:run_id, 3, 'light_scan',  '경량 보안 검사'),
    (:run_id, 4, 'test',        '테스트'),
    (:run_id, 5, 'deep_scan',   '심화 보안 검사'),
    (:run_id, 6, 'build',       '빌드'),
    (:run_id, 7, 'deploy',      '배포');

-- 단계 완료 처리 (예: 1단계 "레포지토리 클론" 성공, 10초)
UPDATE pipeline_steps
SET status = 'success',
    duration_seconds = 10,
    started_at = NOW() - INTERVAL '10 seconds',
    finished_at = NOW()
WHERE pipeline_run_id = :run_id AND step_order = 1;

-- 화면 2: 특정 Run의 7단계 진행 순서대로 조회
SELECT step_order, step_name_ko, status, duration_seconds
FROM pipeline_steps
WHERE pipeline_run_id = :run_id
ORDER BY step_order;
```

---

### 3.5 security_scans

파이프라인 1회당 1개. **화면 3의 보안 점수 카드 / 파이 차트 카운트** 원천.

```sql
CREATE TABLE security_scans (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_run_id     BIGINT NOT NULL UNIQUE REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    scan_uuid           VARCHAR(100) NOT NULL UNIQUE,             -- "f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n"
    security_score      SMALLINT NOT NULL,                        -- 62
    threshold           SMALLINT NOT NULL DEFAULT 75,             -- 75
    pipeline_result     VARCHAR(20) NOT NULL,                     -- 'passed' | 'failed'
    result_message      VARCHAR(300),                             -- "보안 취약점이 발견되어 파이프라인을 종료하였습니다."
    total_findings      INTEGER NOT NULL DEFAULT 0,               -- 61
    critical_count      INTEGER NOT NULL DEFAULT 0,               -- 4
    high_count          INTEGER NOT NULL DEFAULT 0,               -- 11
    medium_count        INTEGER NOT NULL DEFAULT 0,               -- 19
    low_count           INTEGER NOT NULL DEFAULT 0,               -- 27
    scanned_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_scan_result CHECK (pipeline_result IN ('passed','failed')),
    CONSTRAINT chk_score_range CHECK (security_score BETWEEN 0 AND 100),
    CONSTRAINT chk_threshold_range CHECK (threshold BETWEEN 0 AND 100)
);

CREATE INDEX idx_scans_run ON security_scans(pipeline_run_id);
CREATE INDEX idx_scans_uuid ON security_scans(scan_uuid);
CREATE INDEX idx_scans_result ON security_scans(pipeline_result);

COMMENT ON TABLE security_scans IS '보안 분석 결과 헤더 (화면 3 상단 카드)';
```

**예시**

```sql
-- 스캔 결과 저장
INSERT INTO security_scans (
    pipeline_run_id, scan_uuid, security_score, threshold,
    pipeline_result, result_message,
    total_findings, critical_count, high_count, medium_count, low_count
) VALUES (
    :run_id,
    'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n',
    62, 75,
    'failed', '보안 취약점이 발견되어 파이프라인을 종료하였습니다.',
    61, 4, 11, 19, 27
) RETURNING id;

-- 화면 3: 상단 카드 + 파이차트 한 방에
SELECT
    r.full_name,
    pr.branch,
    s.scan_uuid,
    s.security_score,
    s.threshold,
    s.pipeline_result,
    s.result_message,
    s.total_findings,
    s.critical_count, s.high_count, s.medium_count, s.low_count
FROM security_scans s
JOIN pipeline_runs pr ON pr.id = s.pipeline_run_id
JOIN repositories  r  ON r.id = pr.repository_id
WHERE s.scan_uuid = 'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n';
```

---

### 3.6 security_findings

**화면 3 하단 "탐지된 취약점" 목록의 각 행.** SQL Injection 카드 같은.

```sql
CREATE TABLE security_findings (
    id                  BIGSERIAL PRIMARY KEY,
    security_scan_id    BIGINT NOT NULL REFERENCES security_scans(id) ON DELETE CASCADE,
    severity            VARCHAR(10) NOT NULL,                     -- critical/high/medium/low
    cve_id              VARCHAR(30),                              -- "CVE-2003-0041"
    title               VARCHAR(200) NOT NULL,                    -- "SQL Injection"
    cvss_version        VARCHAR(10),                              -- "V2.0"
    cvss_score          NUMERIC(3,1),                             -- 7.5
    file_path           VARCHAR(500) NOT NULL,                    -- "/var/folders/DVWA_.../low.php"
    line_number         INTEGER,                                  -- 35
    description         TEXT NOT NULL,                            -- "Executing non-constant commands..."
    rule_id             VARCHAR(100),                             -- 스캐너 rule id (semgrep/gitleaks)
    scanner             VARCHAR(30),                              -- "semgrep" | "gitleaks"
    code_snippet        TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_severity CHECK (severity IN ('critical','high','medium','low')),
    CONSTRAINT chk_cvss CHECK (cvss_score IS NULL OR cvss_score BETWEEN 0 AND 10)
);

CREATE INDEX idx_findings_scan ON security_findings(security_scan_id);
CREATE INDEX idx_findings_severity ON security_findings(severity);
CREATE INDEX idx_findings_cve ON security_findings(cve_id);
CREATE INDEX idx_findings_scan_sev ON security_findings(security_scan_id, severity);

COMMENT ON TABLE security_findings IS '개별 취약점 레코드 (화면 3 "탐지된 취약점" 목록)';
```

**예시**

```sql
-- 취약점 1건 저장 (화면 3의 SQL Injection 카드와 동일)
INSERT INTO security_findings (
    security_scan_id, severity, cve_id, title,
    cvss_version, cvss_score,
    file_path, line_number,
    description, scanner, rule_id
) VALUES (
    :scan_id,
    'high',
    'CVE-2003-0041',
    'SQL Injection',
    'V2.0', 7.5,
    '/var/folders/DVWA_9241efe5/vulnerabilities/sac/source/low.php',
    35,
    'Executing non-constant commands. This can lead to command injection. You should use ''escapeshellarg()'' when using command.',
    'semgrep',
    'php.lang.security.exec-use.exec-use'
) RETURNING id;

-- 화면 3: 취약점 목록 (심각도 높은 순)
SELECT
    severity, cve_id, title,
    cvss_version, cvss_score,
    file_path, line_number,
    description
FROM security_findings
WHERE security_scan_id = :scan_id
ORDER BY
    CASE severity
        WHEN 'critical' THEN 1
        WHEN 'high'     THEN 2
        WHEN 'medium'   THEN 3
        WHEN 'low'      THEN 4
    END,
    cvss_score DESC NULLS LAST;

-- 화면 1: 탐지된 취약점 총 수
SELECT COUNT(*) AS total_vulnerabilities FROM security_findings;
```

---

### 3.7 ai_suggestions

**화면 3 하단 "AI 제안" 박스.** finding 1건당 0~1개.

```sql
CREATE TABLE ai_suggestions (
    id                      BIGSERIAL PRIMARY KEY,
    security_finding_id     BIGINT NOT NULL UNIQUE REFERENCES security_findings(id) ON DELETE CASCADE,
    suggestion_text         TEXT NOT NULL,
    suggested_fix_code      TEXT,                                 -- 옵션: 실제 수정 코드 블록
    model_name              VARCHAR(50),                          -- "claude-opus-4-6"
    confidence              NUMERIC(3,2),                         -- 0.00 ~ 1.00
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

CREATE INDEX idx_ai_finding ON ai_suggestions(security_finding_id);

COMMENT ON TABLE ai_suggestions IS 'AI 생성 수정 제안 (화면 3 AI 제안 박스)';
```

**예시**

```sql
-- AI 제안 저장
INSERT INTO ai_suggestions (security_finding_id, suggestion_text, model_name, confidence)
VALUES (
    :finding_id,
    '해당 라인의 코드가 SQL 인젝션 위험이 존재하였다. 따라서 이렇게 재작성 하여라 구체적 수정 해보면 좋을 것 같다.',
    'claude-opus-4-6',
    0.92
);

-- 화면 3: 취약점 + AI 제안 묶어서 조회
SELECT
    f.severity, f.cve_id, f.title, f.cvss_score,
    f.file_path, f.line_number, f.description,
    a.suggestion_text AS ai_suggestion
FROM security_findings f
LEFT JOIN ai_suggestions a ON a.security_finding_id = f.id
WHERE f.security_scan_id = :scan_id
ORDER BY f.cvss_score DESC NULLS LAST;
```

---

### 3.8 platform_stats (뷰)

**화면 1의 3개 숫자를 한 번에.** 실시간 계산 뷰.

```sql
CREATE OR REPLACE VIEW platform_stats AS
SELECT
    (SELECT COUNT(*) FROM repositories WHERE is_active = TRUE)        AS analyzed_projects,
    (SELECT COUNT(*) FROM security_findings)                          AS detected_vulnerabilities,
    (SELECT COALESCE(AVG(duration_seconds), 0)::INT
       FROM pipeline_runs
      WHERE status = 'success'
        AND finished_at >= NOW() - INTERVAL '30 days')                AS avg_duration_seconds;

COMMENT ON VIEW platform_stats IS '메인 대시보드 상단 3개 카드 통계 (화면 1)';
```

**예시**

```sql
-- 화면 1: 한 번의 조회로 3개 카드 값 모두 획득
SELECT
    analyzed_projects,                                                -- 12345
    detected_vulnerabilities,                                         -- 8123
    avg_duration_seconds,                                             -- 227
    (avg_duration_seconds / 60) || 'm ' ||
    (avg_duration_seconds % 60) || 's' AS avg_duration_display        -- "3m 47s"
FROM platform_stats;
```

---

## 화면별 조회 쿼리

### 화면 1 — 메인 대시보드

```sql
SELECT
    analyzed_projects,
    detected_vulnerabilities,
    (avg_duration_seconds / 60) || 'm ' ||
    (avg_duration_seconds % 60) || 's' AS avg_analysis_time
FROM platform_stats;
```

### 화면 2 — 파이프라인 진행 (myuser/web-app Run #643033)

```sql
-- 2-1. 헤더
SELECT
    r.full_name,                                   -- "myuser/web-app"
    pr.branch,                                     -- "main"
    r.default_branch,                              -- "main" (default 표시용)
    pr.run_number,                                 -- 643033
    pr.status,                                     -- "success"
    pr.duration_seconds,                           -- 143
    pr.steps_passed || '/' || pr.steps_total AS progress_text   -- "7/7"
FROM pipeline_runs pr
JOIN repositories r ON r.id = pr.repository_id
WHERE r.full_name = 'myuser/web-app'
  AND pr.run_number = 643033;

-- 2-2. 7단계 진행 목록
SELECT step_order, step_name_ko, status, duration_seconds
FROM pipeline_steps ps
JOIN pipeline_runs pr ON pr.id = ps.pipeline_run_id
JOIN repositories r ON r.id = pr.repository_id
WHERE r.full_name = 'myuser/web-app'
  AND pr.run_number = 643033
ORDER BY step_order;
```

### 화면 3 — 보안 분석 결과

```sql
-- 3-1. 헤더 + 점수 카드 + 파이차트 카운트
SELECT
    r.full_name,
    pr.branch,
    s.scan_uuid,
    s.pipeline_result,
    s.result_message,
    s.security_score,
    s.threshold,
    s.total_findings,
    s.critical_count,
    s.high_count,
    s.medium_count,
    s.low_count
FROM security_scans s
JOIN pipeline_runs pr ON pr.id = s.pipeline_run_id
JOIN repositories  r  ON r.id = pr.repository_id
WHERE s.scan_uuid = 'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n';

-- 3-2. 탐지된 취약점 목록 + AI 제안
SELECT
    f.severity,
    f.cve_id,
    f.title,
    f.cvss_version,
    f.cvss_score,
    f.file_path,
    f.line_number,
    f.description,
    a.suggestion_text
FROM security_findings f
LEFT JOIN ai_suggestions a ON a.security_finding_id = f.id
WHERE f.security_scan_id = (
    SELECT id FROM security_scans
    WHERE scan_uuid = 'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n'
)
ORDER BY
    CASE f.severity
        WHEN 'critical' THEN 1
        WHEN 'high'     THEN 2
        WHEN 'medium'   THEN 3
        WHEN 'low'      THEN 4
    END,
    f.cvss_score DESC NULLS LAST;
```

---

## 부록: 전체 생성 순서 (의존 순서대로)

```sql
-- 1. users
-- 2. repositories          (→ users)
-- 3. pipeline_runs         (→ repositories)
-- 4. pipeline_steps        (→ pipeline_runs)
-- 5. security_scans        (→ pipeline_runs)
-- 6. security_findings     (→ security_scans)
-- 7. ai_suggestions        (→ security_findings)
-- 8. platform_stats 뷰
```

**드롭 순서** (역순):

```sql
DROP VIEW  IF EXISTS platform_stats;
DROP TABLE IF EXISTS ai_suggestions;
DROP TABLE IF EXISTS security_findings;
DROP TABLE IF EXISTS security_scans;
DROP TABLE IF EXISTS pipeline_steps;
DROP TABLE IF EXISTS pipeline_runs;
DROP TABLE IF EXISTS repositories;
DROP TABLE IF EXISTS users;
```

---

**End of Document**
