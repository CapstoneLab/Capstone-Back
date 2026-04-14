# CI/CD 보안 분석 플랫폼 DB 명세서 (v2.0)

**작성일**: 2026-04-11
**대상 DB**: PostgreSQL 15+
**백엔드**: FastAPI (Python)

> 앱의 실제 UI 3화면(메인 대시보드 / 파이프라인 진행 / 보안 분석 결과)을 완전히 지원하는 데이터베이스 스키마입니다.

---

## 📋 목차

1. [전체 구조](#1-전체-구조)
2. [화면별 데이터 매핑](#2-화면별-데이터-매핑)
3. [테이블 정의](#3-테이블-정의)
   - 3.1 [users](#31-users--사용자--oauth-로그인)
   - 3.2 [repositories](#32-repositories--레포지토리)
   - 3.3 [pipeline_runs](#33-pipeline_runs--파이프라인-실행)
   - 3.4 [pipeline_steps](#34-pipeline_steps--7단계-진행)
   - 3.5 [security_scans](#35-security_scans--보안-스캔-결과)
   - 3.6 [security_findings](#36-security_findings--탐지된-취약점)
   - 3.7 [ai_suggestions](#37-ai_suggestions--ai-제안)
4. [화면별 조회 쿼리](#4-화면별-조회-쿼리)
5. [빠른 시작](#5-빠른-시작)

---

## 1. 전체 구조

### 테이블 관계도

```
users (사용자 + OAuth 토큰)
  └─ repositories (레포지토리)
        └─ pipeline_runs (파이프라인 실행)
              ├─ pipeline_steps (7단계 진행)
              └─ security_scans (보안 스캔 결과)
                    └─ security_findings (탐지된 취약점)
                          └─ ai_suggestions (AI 제안)
```

### 테이블 요약

| # | 테이블 | 대응 UI | 핵심 역할 |
|---|---|---|---|
| 1 | **users** | 로그인 화면 + 프로필 | GitHub 사용자 + OAuth 토큰 |
| 2 | **repositories** | 화면 1: 분석된 프로젝트 | 분석 대상 레포 |
| 3 | **pipeline_runs** | 화면 2: Run 헤더 | 파이프라인 1회 실행 |
| 4 | **pipeline_steps** | 화면 2: 7단계 진행 | 각 단계 상태/시간 |
| 5 | **security_scans** | 화면 3: 점수 카드 | 스캔 결과 집계 |
| 6 | **security_findings** | 화면 3: 취약점 카드 | 개별 CVE 항목 |
| 7 | **ai_suggestions** | 화면 3: AI 제안 박스 | 수정 제안 텍스트 |

---

## 2. 화면별 데이터 매핑

### 🖥️ 화면 1 — 메인 대시보드

| UI 값 | 출처 |
|---|---|
| 분석된 프로젝트 `12,345` | `COUNT(repositories)` |
| 탐지된 취약점 `8,000+` | `COUNT(security_findings)` |
| 평균 분석 시간 `3m 47s` | `AVG(pipeline_runs.duration_seconds)` |

### 🖥️ 화면 2 — 파이프라인 진행 (myuser/web-app)

| UI 값 | 출처 |
|---|---|
| `myuser/web-app` | `repositories.full_name` |
| `main(default)` | `pipeline_runs.branch` + `repositories.default_branch` |
| `Run #643033` | `pipeline_runs.run_number` |
| `2m 23s` | `pipeline_runs.duration_seconds` |
| `7/7 파이프라인 통과` | `pipeline_runs.steps_passed / steps_total` |
| 7단계 리스트 (클론 10s, 설치 20s, ...) | `pipeline_steps` |

### 🖥️ 화면 3 — 보안 분석 결과

| UI 값 | 출처 |
|---|---|
| `f3fk34432h-h4lo56j34-...` | `security_scans.scan_uuid` |
| 파이프라인 실패 배너 | `security_scans.pipeline_result + result_message` |
| 보안 점수 `62 / 100` | `security_scans.security_score` |
| 쓰레스홀드 `75/100` | `security_scans.threshold` |
| 파이차트 (C:4, H:11, M:19, L:27) | `security_scans.critical/high/medium/low_count` |
| `HIGH CVE-2003-0041 SQL Injection` | `security_findings` |
| `CVSS V2.0: 7.5 / 10.0` | `security_findings.cvss_version + cvss_score` |
| 파일 경로 / 라인 | `security_findings.file_path + line_number` |
| AI 제안 박스 | `ai_suggestions.suggestion_text` |

---

## 3. 테이블 정의

각 테이블마다 다음을 제공합니다:

- 📌 **용도**: 어떤 UI 요소를 지원하는지
- 🔧 **CREATE 쿼리**: 테이블 생성 SQL
- 💾 **샘플 INSERT**: 실제 UI 값을 넣는 예시
- 👀 **결과 데이터**: INSERT 후 테이블에 저장된 실제 row

---

### 3.1 users — 사용자 + OAuth 로그인

📌 **용도**: GitHub 사용자 정보 + OAuth 소셜 로그인 토큰을 함께 보관합니다. 앱 로그인 화면의 **"GitHub로 계속하기"** 후 GitHub API 응답(`github_id`, `login`, `avatar_url`)과 발급받은 `access_token`(Fernet 암호화)이 이 테이블에 upsert 됩니다.

> ⚠️ `github_access_token_encrypted`는 Fernet(AES-128-CBC + HMAC-SHA256)으로 암호화된 값. 평문 저장 금지.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE users (
    id                            BIGSERIAL PRIMARY KEY,
    github_id                     BIGINT       NOT NULL UNIQUE,
    github_login                  VARCHAR(100) NOT NULL UNIQUE,
    display_name                  VARCHAR(150),
    avatar_url                    VARCHAR(500),
    email                         VARCHAR(200),
    github_access_token_encrypted TEXT,
    created_at                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_github_id ON users(github_id);
CREATE INDEX idx_users_login     ON users(github_login);
```

**컬럼 설명**

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `github_id` | BIGINT NOT NULL UNIQUE | 불변 식별자 — login은 변경 가능하지만 이 숫자 ID는 불변. upsert 키로 사용 |
| `github_login` | VARCHAR(100) UNIQUE | display용 핸들 (myuser). 변경될 수 있으므로 식별자로 쓰지 않음 |
| `github_access_token_encrypted` | TEXT | Fernet 암호화된 OAuth 토큰. 레포 조회 등 GitHub API 호출 시 복호화해서 사용 |
| `updated_at` | TIMESTAMP | 마지막 로그인/토큰 갱신 시각 |

💾 **샘플 INSERT** (GitHub OAuth 콜백 후 upsert)

```sql
INSERT INTO users (github_id, github_login, display_name, avatar_url, email, github_access_token_encrypted)
VALUES
    (12345678, 'myuser',    'My User',   'https://github.com/myuser.png',    'myuser@example.com',
     'gAAAAABk_FERNET_ENCRYPTED_PLACEHOLDER_xxxxxxxxxxxxxxxxxxxxxxxxxxxx'),
    (23456789, 'alice-dev', 'Alice Kim', 'https://github.com/alice-dev.png', 'alice@example.com',
     'gAAAAABk_FERNET_ENCRYPTED_PLACEHOLDER_yyyyyyyyyyyyyyyyyyyyyyyyyyyy'),
    (34567890, 'bob99',     'Bob Park',  'https://github.com/bob99.png',     'bob@example.com',
     'gAAAAABk_FERNET_ENCRYPTED_PLACEHOLDER_zzzzzzzzzzzzzzzzzzzzzzzzzzzz');
```

👀 **결과 데이터**

| id | github_id | github_login | display_name | token (preview) | updated_at |
|---|---|---|---|---|---|
| 1 | 12345678 | myuser | My User | gAAAAABk_FERNET... | 2026-04-12 11:04:57 |
| 2 | 23456789 | alice-dev | Alice Kim | gAAAAABk_FERNET... | 2026-04-12 11:04:57 |
| 3 | 34567890 | bob99 | Bob Park | gAAAAABk_FERNET... | 2026-04-12 11:04:57 |

→ `github_id`가 upsert 키이므로, 같은 GitHub 계정으로 재로그인하면 `github_login`, `avatar_url`, `github_access_token_encrypted`, `updated_at`이 갱신됩니다.

---

### 3.2 repositories — 레포지토리

📌 **용도**: 분석 대상 GitHub 레포지토리. **화면 1의 "분석된 프로젝트 12,345"** 카운트 대상이며, **화면 2의 `myuser/web-app`** 레포 정보를 제공합니다.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE repositories (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repo_name       VARCHAR(200) NOT NULL,
    full_name       VARCHAR(300) NOT NULL UNIQUE,
    repo_url        VARCHAR(500) NOT NULL,
    default_branch  VARCHAR(100) NOT NULL DEFAULT 'main',
    language        VARCHAR(50),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, repo_name)
);

CREATE INDEX idx_repos_user      ON repositories(user_id);
CREATE INDEX idx_repos_full_name ON repositories(full_name);
CREATE INDEX idx_repos_active    ON repositories(is_active) WHERE is_active = TRUE;
```

💾 **샘플 INSERT**

```sql
INSERT INTO repositories (user_id, repo_name, full_name, repo_url, default_branch, language)
VALUES
    (1, 'web-app',  'myuser/web-app',     'https://github.com/myuser/web-app',     'main', 'PHP'),
    (1, 'api-svc',  'myuser/api-svc',     'https://github.com/myuser/api-svc',     'main', 'Python'),
    (2, 'frontend', 'alice-dev/frontend', 'https://github.com/alice-dev/frontend', 'dev',  'TypeScript');
```

👀 **결과 데이터**

| id | user_id | repo_name | full_name | default_branch | language | is_active |
|---|---|---|---|---|---|---|
| 1 | 1 | web-app | **myuser/web-app** | main | PHP | true |
| 2 | 1 | api-svc | myuser/api-svc | main | Python | true |
| 3 | 2 | frontend | alice-dev/frontend | dev | TypeScript | true |

---

### 3.3 pipeline_runs — 파이프라인 실행

📌 **용도**: 파이프라인 1회 실행 단위. **화면 2의 `Run #643033`, `2m 23s`, `7/7 통과`** 헤더 값의 원천.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE pipeline_runs (
    id                  BIGSERIAL PRIMARY KEY,
    repository_id       BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    run_number          INTEGER NOT NULL,
    run_uuid            UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    branch              VARCHAR(100) NOT NULL,
    commit_sha          VARCHAR(40),
    trigger_type        VARCHAR(30) NOT NULL DEFAULT 'push',
    status              VARCHAR(20) NOT NULL DEFAULT 'queued',
    steps_total         SMALLINT NOT NULL DEFAULT 7,
    steps_passed        SMALLINT NOT NULL DEFAULT 0,
    duration_seconds    INTEGER,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (repository_id, run_number),
    CONSTRAINT chk_run_status CHECK (status IN ('queued','running','success','failed','cancelled'))
);

CREATE INDEX idx_runs_repo_created ON pipeline_runs(repository_id, created_at DESC);
CREATE INDEX idx_runs_status       ON pipeline_runs(status);
CREATE INDEX idx_runs_uuid         ON pipeline_runs(run_uuid);
```

💾 **샘플 INSERT**

```sql
-- 화면 2에 보이는 Run #643033 (myuser/web-app, 2m 23s, 7/7 성공)
INSERT INTO pipeline_runs (
    repository_id, run_number, branch, commit_sha, trigger_type,
    status, steps_passed, duration_seconds, started_at, finished_at
) VALUES
    (1, 643033, 'main', 'a1b2c3d4e5f6789', 'push',
     'success', 7, 143, '2026-04-11 09:10:00', '2026-04-11 09:12:23'),
    (1, 643034, 'main', 'b2c3d4e5f67890a', 'push',
     'failed',  5, 98,  '2026-04-11 09:30:00', '2026-04-11 09:31:38'),
    (2, 100,    'main', 'c3d4e5f67890ab1', 'manual',
     'running', 3, NULL,'2026-04-11 09:35:00', NULL);
```

👀 **결과 데이터**

| id | repository_id | run_number | branch | status | steps_passed | duration_seconds |
|---|---|---|---|---|---|---|
| 1 | 1 | **643033** | main | success | **7** / 7 | **143** (2m 23s) |
| 2 | 1 | 643034 | main | failed | 5 / 7 | 98 |
| 3 | 2 | 100 | main | running | 3 / 7 | NULL |

---

### 3.4 pipeline_steps — 7단계 진행

📌 **용도**: **화면 2의 7단계 진행 항목** (레포지토리 클론 10s, 의존성 설치 20s, ...). 각 파이프라인마다 고정 7개 row.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE pipeline_steps (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_run_id     BIGINT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    step_order          SMALLINT NOT NULL,
    step_key            VARCHAR(40) NOT NULL,
    step_name_ko        VARCHAR(50) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    duration_seconds    INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,

    UNIQUE (pipeline_run_id, step_order),
    CONSTRAINT chk_step_order  CHECK (step_order BETWEEN 1 AND 7),
    CONSTRAINT chk_step_status CHECK (status IN ('pending','running','success','failed','skipped'))
);

CREATE INDEX idx_steps_run ON pipeline_steps(pipeline_run_id, step_order);
```

💾 **샘플 INSERT**

```sql
-- Run #643033 의 7단계 (화면 2의 초록 체크 리스트 그대로)
INSERT INTO pipeline_steps (pipeline_run_id, step_order, step_key, step_name_ko, status, duration_seconds)
VALUES
    (1, 1, 'clone',      '레포지토리 클론', 'success', 10),
    (1, 2, 'install',    '의존성 설치',     'success', 20),
    (1, 3, 'light_scan', '경량 보안 검사',  'success', 14),
    (1, 4, 'test',       '테스트',          'success', 28),
    (1, 5, 'deep_scan',  '심화 보안 검사',  'success', 22),
    (1, 6, 'build',      '빌드',            'success', 31),
    (1, 7, 'deploy',     '배포',            'success', 18);
```

👀 **결과 데이터**

| id | pipeline_run_id | step_order | step_name_ko | status | duration_seconds |
|---|---|---|---|---|---|
| 1 | 1 | 1 | 레포지토리 클론 | ✅ success | 10 |
| 2 | 1 | 2 | 의존성 설치 | ✅ success | 20 |
| 3 | 1 | 3 | 경량 보안 검사 | ✅ success | 14 |
| 4 | 1 | 4 | 테스트 | ✅ success | 28 |
| 5 | 1 | 5 | 심화 보안 검사 | ✅ success | 22 |
| 6 | 1 | 6 | 빌드 | ✅ success | 31 |
| 7 | 1 | 7 | 배포 | ✅ success | 18 |

**합계**: 10 + 20 + 14 + 28 + 22 + 31 + 18 = **143초 = 2m 23s** ✨ (화면 2 헤더와 일치)

---

### 3.5 security_scans — 보안 스캔 결과

📌 **용도**: **화면 3 상단의 보안 점수 카드 + 취약점 파이차트** 원천. 파이프라인 1회당 1개 row.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE security_scans (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_run_id     BIGINT NOT NULL UNIQUE REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    scan_uuid           VARCHAR(100) NOT NULL UNIQUE,
    security_score      SMALLINT NOT NULL,
    threshold           SMALLINT NOT NULL DEFAULT 75,
    pipeline_result     VARCHAR(20) NOT NULL,
    result_message      VARCHAR(300),
    total_findings      INTEGER NOT NULL DEFAULT 0,
    critical_count      INTEGER NOT NULL DEFAULT 0,
    high_count          INTEGER NOT NULL DEFAULT 0,
    medium_count        INTEGER NOT NULL DEFAULT 0,
    low_count           INTEGER NOT NULL DEFAULT 0,
    scanned_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_scan_result CHECK (pipeline_result IN ('passed','failed')),
    CONSTRAINT chk_score_range CHECK (security_score BETWEEN 0 AND 100),
    CONSTRAINT chk_threshold   CHECK (threshold BETWEEN 0 AND 100)
);

CREATE INDEX idx_scans_run    ON security_scans(pipeline_run_id);
CREATE INDEX idx_scans_uuid   ON security_scans(scan_uuid);
CREATE INDEX idx_scans_result ON security_scans(pipeline_result);
```

💾 **샘플 INSERT**

```sql
-- 화면 3 상단 카드 (점수 62/75, 파이차트 4/11/19/27)
INSERT INTO security_scans (
    pipeline_run_id, scan_uuid,
    security_score, threshold, pipeline_result, result_message,
    total_findings, critical_count, high_count, medium_count, low_count
) VALUES (
    2,
    'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n',
    62, 75, 'failed',
    '보안 취약점이 발견되어 파이프라인을 종료하였습니다.',
    61, 4, 11, 19, 27
);
```

👀 **결과 데이터**

| id | scan_uuid | security_score | threshold | pipeline_result | critical | high | medium | low | total |
|---|---|---|---|---|---|---|---|---|---|
| 1 | f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n | **62** | **75** | ❌ failed | **4** | **11** | **19** | **27** | **61** |

→ 화면 3의 원형 게이지 62/100, 파이차트(Critical 4 / High 11 / Medium 19 / Low 27 = total 61)와 정확히 일치.

---

### 3.6 security_findings — 탐지된 취약점

📌 **용도**: **화면 3 하단 "탐지된 취약점" 카드 목록**. 각 row가 카드 1개 (SQL Injection 카드 등).

🔧 **CREATE 쿼리**

```sql
CREATE TABLE security_findings (
    id                  BIGSERIAL PRIMARY KEY,
    security_scan_id    BIGINT NOT NULL REFERENCES security_scans(id) ON DELETE CASCADE,
    severity            VARCHAR(10) NOT NULL,
    cve_id              VARCHAR(30),
    title               VARCHAR(200) NOT NULL,
    cvss_version        VARCHAR(10),
    cvss_score          NUMERIC(3,1),
    file_path           VARCHAR(500) NOT NULL,
    line_number         INTEGER,
    description         TEXT NOT NULL,
    rule_id             VARCHAR(100),
    scanner             VARCHAR(30),
    code_snippet        TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_severity CHECK (severity IN ('critical','high','medium','low')),
    CONSTRAINT chk_cvss     CHECK (cvss_score IS NULL OR cvss_score BETWEEN 0 AND 10)
);

CREATE INDEX idx_findings_scan     ON security_findings(security_scan_id);
CREATE INDEX idx_findings_severity ON security_findings(severity);
CREATE INDEX idx_findings_cve      ON security_findings(cve_id);
CREATE INDEX idx_findings_scan_sev ON security_findings(security_scan_id, severity);
```

💾 **샘플 INSERT**

```sql
-- 화면 3의 "SQL Injection" 카드 + 다른 취약점 예시들
INSERT INTO security_findings (
    security_scan_id, severity, cve_id, title,
    cvss_version, cvss_score, file_path, line_number,
    description, scanner, rule_id
) VALUES
    (1, 'high', 'CVE-2003-0041', 'SQL Injection',
     'V2.0', 7.5,
     '/var/folders/DVWA_9241efe5/vulnerabilities/sac/source/low.php', 35,
     'Executing non-constant commands. This can lead to command injection. You should use ''escapeshellarg()'' when using command.',
     'semgrep', 'php.lang.security.exec-use.exec-use'),

    (1, 'critical', 'CVE-2021-44228', 'Log4Shell RCE',
     'V3.1', 10.0,
     '/app/src/main/java/Logger.java', 87,
     'Remote code execution via Log4j JNDI lookup.',
     'semgrep', 'java.log4j.log4shell'),

    (1, 'medium', NULL, 'Hardcoded Secret',
     NULL, 5.3,
     '/config/database.php', 12,
     'Database password is hardcoded. Move it to environment variables.',
     'gitleaks', 'generic-api-key');
```

👀 **결과 데이터**

| id | severity | cve_id | title | cvss_score | file_path | line |
|---|---|---|---|---|---|---|
| 1 | 🟧 **high** | **CVE-2003-0041** | **SQL Injection** | **7.5** | `/var/folders/DVWA_9241efe5/vulnerabilities/sac/source/low.php` | **35** |
| 2 | 🟥 critical | CVE-2021-44228 | Log4Shell RCE | 10.0 | `/app/src/main/java/Logger.java` | 87 |
| 3 | 🟨 medium | NULL | Hardcoded Secret | 5.3 | `/config/database.php` | 12 |

→ 1번 row가 화면 3에 보이는 **HIGH / CVE-2003-0041 / SQL Injection / CVSS V2.0: 7.5** 카드와 완전히 일치.

---

### 3.7 ai_suggestions — AI 제안

📌 **용도**: **화면 3의 "AI 제안" 초록 박스**. 각 취약점당 최대 1개.

🔧 **CREATE 쿼리**

```sql
CREATE TABLE ai_suggestions (
    id                      BIGSERIAL PRIMARY KEY,
    security_finding_id     BIGINT NOT NULL UNIQUE REFERENCES security_findings(id) ON DELETE CASCADE,
    suggestion_text         TEXT NOT NULL,
    suggested_fix_code      TEXT,
    model_name              VARCHAR(50),
    confidence              NUMERIC(3,2),
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

CREATE INDEX idx_ai_finding ON ai_suggestions(security_finding_id);
```

💾 **샘플 INSERT**

```sql
-- 화면 3의 SQL Injection 카드 아래 "AI 제안" 박스 내용
INSERT INTO ai_suggestions (
    security_finding_id, suggestion_text, suggested_fix_code, model_name, confidence
) VALUES
    (1,
     '해당 라인의 코드가 SQL 인젝션 위험이 존재하였다. 따라서 이렇게 재작성 하여라 구체적 수정 해보면 좋을 것 같다.',
     '$stmt = $pdo->prepare("SELECT * FROM users WHERE id = ?");' || chr(10) ||
     '$stmt->execute([$user_id]);',
     'claude-opus-4-6', 0.92),

    (2,
     'Log4j 버전을 2.17.0 이상으로 업그레이드하고, JNDI lookup 기능을 비활성화 하는 것이 필요하다.',
     '<dependency><groupId>org.apache.logging.log4j</groupId><artifactId>log4j-core</artifactId><version>2.17.2</version></dependency>',
     'claude-opus-4-6', 0.98);
```

👀 **결과 데이터**

| id | finding_id | suggestion_text | model_name | confidence |
|---|---|---|---|---|
| 1 | 1 | 해당 라인의 코드가 SQL 인젝션 위험이 존재하였다. 따라서 이렇게 재작성 하여라 구체적 수정 해보면 좋을 것 같다. | claude-opus-4-6 | 0.92 |
| 2 | 2 | Log4j 버전을 2.17.0 이상으로 업그레이드하고, JNDI lookup 기능을 비활성화 하는 것이 필요하다. | claude-opus-4-6 | 0.98 |

→ 1번 row가 화면 3의 AI 제안 박스 텍스트와 정확히 일치.

---

## 4. 화면별 조회 쿼리

### 🖥️ 화면 1 — 메인 대시보드 (3개 카드 한 번에)

```sql
SELECT
    (SELECT COUNT(*) FROM repositories WHERE is_active = TRUE)  AS analyzed_projects,
    (SELECT COUNT(*) FROM security_findings)                    AS detected_vulnerabilities,
    (SELECT
        (AVG(duration_seconds) / 60)::INT || 'm ' ||
        (AVG(duration_seconds)::INT % 60)  || 's'
     FROM pipeline_runs
     WHERE status = 'success') AS avg_analysis_time;
```

**결과 예시**

| analyzed_projects | detected_vulnerabilities | avg_analysis_time |
|---|---|---|
| 12345 | 8123 | 3m 47s |

---

### 🖥️ 화면 2 — 파이프라인 진행 (myuser/web-app Run #643033)

**헤더 조회**

```sql
SELECT
    r.full_name,                                        -- "myuser/web-app"
    pr.branch,                                          -- "main"
    r.default_branch,
    pr.run_number,                                      -- 643033
    pr.status,                                          -- "success"
    (pr.duration_seconds / 60) || 'm ' ||
    (pr.duration_seconds % 60) || 's' AS duration,      -- "2m 23s"
    pr.steps_passed || '/' || pr.steps_total AS progress -- "7/7"
FROM pipeline_runs pr
JOIN repositories  r ON r.id = pr.repository_id
WHERE r.full_name = 'myuser/web-app' AND pr.run_number = 643033;
```

**7단계 리스트 조회**

```sql
SELECT step_order, step_name_ko, status, duration_seconds
FROM pipeline_steps
WHERE pipeline_run_id = 1
ORDER BY step_order;
```

---

### 🖥️ 화면 3 — 보안 분석 결과

**상단 카드 + 파이차트**

```sql
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
JOIN pipeline_runs  pr ON pr.id = s.pipeline_run_id
JOIN repositories   r  ON r.id  = pr.repository_id
WHERE s.scan_uuid = 'f3fk34432h-h4lo56j34-5oi3j56r-tijw4h3n';
```

**취약점 목록 + AI 제안**

```sql
SELECT
    f.severity,
    f.cve_id,
    f.title,
    f.cvss_version,
    f.cvss_score,
    f.file_path,
    f.line_number,
    f.description,
    a.suggestion_text AS ai_suggestion
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

## 5. 빠른 시작

### Windows (PowerShell)

```powershell
cd c:\Users\fprh2\Desktop\capstone

.\db-manage.ps1 start     # PostgreSQL 컨테이너 시작 (포트 5433)
.\db-manage.ps1 check     # 상태 확인
.\db-manage.ps1 tables    # 테이블 목록 조회
.\db-manage.ps1 help      # 전체 명령어
```

### Linux / Mac

```bash
make db-start
make db-check
make db-tables
```

### 접속 정보

| 항목 | 값 |
|---|---|
| Host | `localhost` |
| Port | `5433` |
| Database | `capstone` |
| User | `capstone` |

### 테이블 생성 순서 (의존성 순)

```
1. users
2. repositories       (→ users)
3. pipeline_runs      (→ repositories)
4. pipeline_steps     (→ pipeline_runs)
5. security_scans     (→ pipeline_runs)
6. security_findings  (→ security_scans)
7. ai_suggestions     (→ security_findings)
```

삭제는 역순(`ai_suggestions → users`)으로 진행하거나, `CASCADE` 덕분에 `users` 삭제만으로 모든 하위 데이터가 자동 정리됩니다.

---

**End of Document**
