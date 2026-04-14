#!/usr/bin/env python3
"""
노션용 ER 다이어그램 코드 모음
사용 방법:
1. https://mermaid.live 열기
2. 아래 코드 복붙
3. "Download SVG" 클릭
4. Notion에 이미지 업로드
"""

# ============================================================================
# Mermaid ERDiagram - 노션용
# ============================================================================

MERMAID_ER_DIAGRAM = """
erDiagram
    PIPELINE_JOBS ||--o{ PIPELINE_STEPS : contains
    PIPELINE_JOBS ||--o{ SECURITY_FINDINGS : scans
    PIPELINE_JOBS ||--o{ STEP_LOGS : generates
    PIPELINE_JOBS ||--o{ BUILD_ARTIFACTS : produces
    PIPELINE_JOBS ||--o{ DEPLOYMENTS : triggers
    PIPELINE_JOBS ||--o{ SECURITY_SUMMARY : summarizes
    PIPELINE_STEPS ||--o{ STEP_LOGS : writes
    PIPELINE_STEPS ||--o{ SECURITY_FINDINGS : detects
    BUILD_ARTIFACTS ||--o{ DEPLOYMENTS : deployed

    PIPELINE_JOBS {
        uuid job_id PK "고유 작업 ID"
        string repo_url "저장소 URL"
        string branch "브랜치명"
        string status "상태"
        string overall_result "최종 결과"
        timestamp created_at "생성 시각"
        timestamp started_at "시작 시각"
        timestamp completed_at "완료 시각"
        int duration_secs "소요 시간"
        jsonb metadata "메타데이터"
    }

    PIPELINE_STEPS {
        uuid step_id PK "단계 ID"
        uuid job_id FK "작업 ID"
        string step_name "단계명"
        string status "상태"
        string error_message "에러 메시지"
        timestamp started_at "시작"
        timestamp ended_at "종료"
        float duration_secs "소요 시간"
    }

    STEP_LOGS {
        bigserial log_id PK "로그 ID"
        uuid job_id FK "작업 ID"
        uuid step_id FK "단계 ID"
        string log_level "로그 레벨"
        text log_content "로그 내용"
        timestamp timestamp "시간"
    }

    SECURITY_FINDINGS {
        uuid finding_id PK "취약점 ID"
        uuid job_id FK "작업 ID"
        uuid step_id FK "단계 ID"
        string scan_type "스캔 타입"
        string severity "심각도"
        string rule_id "규칙 ID"
        string file_path "파일 경로"
        int line_number "라인 번호"
        text message "메시지"
        float cvss_score "CVSS 점수"
    }

    BUILD_ARTIFACTS {
        uuid artifact_id PK "아티팩트 ID"
        uuid job_id FK "작업 ID"
        uuid step_id FK "단계 ID"
        string artifact_name "아티팩트명"
        string artifact_type "타입"
        string location "저장 위치"
        bigint size_bytes "크기"
        string checksum "체크섬"
    }

    DEPLOYMENTS {
        uuid deployment_id PK "배포 ID"
        uuid job_id FK "작업 ID"
        uuid artifact_id FK "아티팩트 ID"
        string target_env "대상 환경"
        string deployment_status "배포 상태"
        string deployed_by "배포자"
        timestamp deployed_at "배포 시각"
        jsonb deployment_result "결과 정보"
    }

    SECURITY_SUMMARY {
        uuid summary_id PK "요약 ID"
        uuid job_id FK "작업 ID"
        int total_findings "전체 취약점"
        int critical_count "심각도 - Critical"
        int high_count "심각도 - High"
        int medium_count "심각도 - Medium"
        int low_count "심각도 - Low"
        string overall_status "최종 판정"
    }
"""

# ============================================================================
# 텍스트 다이어그램 - 노션 코드 블록용
# ============================================================================

TEXT_DIAGRAM = """
CI/CD Database Architecture - 엔티티 관계도

┌──────────────────────────────┐
│    PIPELINE_JOBS             │
│  (파이프라인 작업 - 중심)     │
│                              │
│  PK: job_id (UUID)           │
│  - repo_url                  │
│  - branch                    │
│  - status                    │
│  - created_at                │
│  - completed_at              │
│  - duration_secs             │
│  - metadata (JSONB)          │
└──────────────┬───────────────┘
               │
    ┌──────────┼────────────────────────────────────┐
    │          │                                    │
    │1:N       │                                    │
    │          │                                    │
    ▼          ▼                                    ▼
┌──────────┐ ┌──────────────┐  ┌──────────────────────────┐
│  STEPS   │ │  LOGS        │  │ SECURITY_FINDINGS        │
│  (7개)   │ │ (상세 로그)  │  │ (gitleaks/semgrep)       │
│          │ │              │  │                          │
│  step_id │ │ log_id       │  │ finding_id               │
│(PK, FK) │ │ (PK)         │  │ (PK, FK)                 │
│ job_id  │ │ job_id (FK)  │  │ job_id (FK)              │
│(FK)     │ │ step_id (FK) │  │ step_id (FK)             │
│ step_   │ │ log_level    │  │ scan_type                │
│ name    │ │ log_content  │  │ severity                 │
│ status  │ │ timestamp    │  │ rule_id                  │
│ error   │ │              │  │ file_path                │
│ message │ │              │  │ line_number              │
│ duration│ │              │  │ message                  │
└────┬────┘ └──────────────┘  │ cwe_id                   │
     │                        │ cvss_score               │
     │                        │ is_masked                │
     │                        └──────────────┬───────────┘
     │                                       │
     └──────────────┬──────────────┬─────────┘
                    │              │
                    ▼              ▼
            ┌─────────────┐  ┌──────────────────┐
            │ ARTIFACTS   │  │ DEPLOYMENTS      │
            │ (빌드결과)  │  │ (배포 이력)      │
            │             │  │                  │
            │ artifact_id │  │ deployment_id    │
            │ (PK, FK)    │  │ (PK, FK)         │
            │ job_id (FK) │  │ job_id (FK)      │
            │ step_id (FK)│  │ artifact_id (FK) │
            │ artifact_   │  │ target_env       │
            │ name        │  │ deployment_      │
            │ artifact_   │  │ status           │
            │ type        │  │ deployed_by      │
            │ location    │  │ deployed_at      │
            │ size_bytes  │  │ deployment_      │
            │ checksum    │  │ result           │
            └─────────────┘  └────────┬─────────┘
                                       │1:1
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │ SECURITY_SUMMARY         │
                        │ (보안 요약 - 캐시)       │
                        │                          │
                        │ summary_id (PK)          │
                        │ job_id (FK, UNIQUE)      │
                        │ total_findings           │
                        │ critical_count           │
                        │ high_count               │
                        │ medium_count             │
                        │ low_count                │
                        │ gitleaks_count           │
                        │ semgrep_count            │
                        │ overall_status           │
                        │ status_reason            │
                        │ calculated_at            │
                        └──────────────────────────┘

범례:
====
1:N  = 1개 pipeline_job은 많은 steps/logs/findings를 가짐
1:1  = 1개 job은 1개 summary를 가짐
PK   = Primary Key (고유키)
FK   = Foreign Key (외래키)
"""

# ============================================================================
# 테이블 구조 - 노션 표용
# ============================================================================

TABLE_STRUCTURE = """
테이블 개요 및 주요 통계

1. PIPELINE_JOBS (파이프라인 작업)
   - 행당 크기: ~500B
   - 월 예상 행: ~1,000
   - 월 크기: ~500KB
   - 역할: 파이프라인 작업 추적 (가장 중요)

2. PIPELINE_STEPS (단계별 진행)
   - 행당 크기: ~300B
   - 월 예상 행: ~7,000 (job당 7개 step)
   - 월 크기: ~2.1MB
   - 역할: clone, install, test, build, deploy 등

3. STEP_LOGS (상세 로그)
   - 행당 크기: ~1KB
   - 월 예상 행: ~70,000
   - 월 크기: ~70MB (주의: 모든 크기의 70% 차지)
   - 역할: stdout/stderr 기록
   - 저장 전략: 배치 저장 (100줄 단위)

4. SECURITY_FINDINGS (보안 취약점)
   - 행당 크기: ~800B
   - 월 예상 행: ~5,000
   - 월 크기: ~4MB
   - 역할: gitleaks (시크릿) + semgrep (코드 취약점)

5. BUILD_ARTIFACTS (빌드 산출물)
   - 행당 크기: ~400B
   - 월 예상 행: ~1,000
   - 월 크기: ~400KB
   - 역할: JAR, Docker, ZIP 등 메타데이터

6. DEPLOYMENTS (배포 이력)
   - 행당 크기: ~300B
   - 월 예상 행: ~500
   - 월 크기: ~150KB
   - 역할: staging/production 배포 기록

7. SECURITY_SUMMARY (보안 요약 캐시)
   - 행당 크기: ~100B
   - 월 예상 행: ~1,000
   - 월 크기: ~100KB
   - 역할: security_findings 집계 (빠른 조회)

총합:
- 월 행 수: ~84,500
- 월 크기: ~77MB
- 연간 크기: ~900MB
"""

# ============================================================================
# 관계도 설명 - 노션 텍스트용
# ============================================================================

RELATIONSHIPS = """
엔티티 관계 설명

1. PIPELINE_JOBS → PIPELINE_STEPS (1:N)
   한 번의 CI/CD 작업(job)은 여러 단계(steps)를 거침
   예: 1개 job = clone, install, test, build, deploy 7개 steps

2. PIPELINE_JOBS → STEP_LOGS (1:N)
   각 단계에서 생성되는 로그들
   예: npm install step → 1000줄의 로그

3. PIPELINE_JOBS → SECURITY_FINDINGS (1:N)
   1개 job에서 여러 보안 이슈 발견 가능
   예: 시크릿 3개 + 취약점 2개 = 5개 findings

4. PIPELINE_JOBS → BUILD_ARTIFACTS (1:N)
   보통 1개 job = 1개 아티팩트
   하지만 다중 아티팩트 가능 (jar + docker 등)

5. PIPELINE_JOBS → DEPLOYMENTS (1:N)
   1개 job → 여러 환경에 배포 (dev, staging, prod)

6. PIPELINE_JOBS → SECURITY_SUMMARY (1:1)
   1개 job에 대해 정확히 1개의 요약 기록
   자동 생성 (트리거로)

7. PIPELINE_STEPS → STEP_LOGS (1:N)
   각 step은 많은 로그 라인 생성
   예: install step → 수백 줄 로그

8. PIPELINE_STEPS → SECURITY_FINDINGS (1:N)
   각 step의 스캔 결과
   예: deep_security_scan step → semgrep 결과 저장

9. BUILD_ARTIFACTS → DEPLOYMENTS (1:N)
   1개 아티팩트를 여러 환경에 배포
   예: backend-0.1.0.jar를 dev, staging, prod에 배포
"""

# ============================================================================
# 다이어그램 사용 방법
# ============================================================================

USAGE_INSTRUCTIONS = """
✅ Mermaid ERDiagram을 Notion에 추가하는 방법

방법 1: 온라인 렌더러 사용 (추천)
1. https://mermaid.live 열기
2. 왼쪽 편집 영역에 MERMAID_ER_DIAGRAM 코드 복붙
3. 오른쪽에 다이어그램 미리보기 나옴
4. 우상단 "Download SVG" 클릭
5. Notion에서:
   - "/" → "Image" 블록 추가
   - 다운로드한 SVG 파일 업로드
   - 크기 조정 (Full width 권장)

방법 2: Notion 코드 블록 사용
1. Notion에서 "/" → "코드" 블록
2. 왼쪽 상단 "언어" → "mermaid" 선택
3. MERMAID_ER_DIAGRAM 코드 붙여넣기
4. Notion이 자동으로 렌더링
5. [참고] Notion 모바일에서는 코드로만 보여짐

방법 3: 텍스트 다이어그램
1. Notion에서 "/" → "코드" 블록
2. 언어 → "text" 선택
3. TEXT_DIAGRAM 붙여넣기
4. 간단한 텍스트 기반 관계도
5. 모든 환경에서 동일하게 보임 (추천하지 않음)

💡 팁:
- SVG 이미지가 가장 예쁨 (방법 1 추천)
- Mermaid 코드는 가장 편함 (방법 2)
- 텍스트는 구닥다리지만 빠름 (방법 3)
"""

# ============================================================================
# 주요 쿼리 예시
# ============================================================================

SAMPLE_QUERIES = """
데이터베이스 쿼리 예시 (SQL)

1. Job 상세 조회 (가장 많이 사용)
SELECT 
    j.job_id,
    j.repo_url,
    j.branch,
    j.status,
    j.created_at,
    j.completed_at,
    COUNT(DISTINCT s.step_id) as step_count,
    COUNT(DISTINCT sf.finding_id) as finding_count
FROM pipeline_jobs j
LEFT JOIN pipeline_steps s ON j.job_id = s.job_id
LEFT JOIN security_findings sf ON j.job_id = sf.job_id
WHERE j.job_id = 'job-uuid-here'
GROUP BY j.job_id;

2. Critical 취약점 찾기
SELECT 
    sf.job_id,
    sf.scan_type,
    sf.rule_id,
    sf.severity,
    sf.file_path,
    sf.message
FROM security_findings sf
WHERE sf.severity = 'critical'
  AND sf.created_at >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY sf.created_at DESC;

3. 최근 배포 이력 (production)
SELECT 
    d.deployment_id,
    d.job_id,
    d.target_env,
    d.deployment_status,
    d.deployed_at,
    ba.artifact_name
FROM deployments d
JOIN build_artifacts ba ON d.artifact_id = ba.artifact_id
WHERE d.target_env = 'production'
  AND d.deployed_at >= CURRENT_DATE - INTERVAL '30 days'
ORDER BY d.deployed_at DESC;

4. 보안 요약 확인
SELECT 
    job_id,
    total_findings,
    critical_count,
    high_count,
    medium_count,
    overall_status
FROM security_summary
WHERE job_id = 'job-uuid-here';

5. 파이프라인 성공률 (이번 달)
SELECT 
    COUNT(*) as total_runs,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
    ROUND(
        100.0 * SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) / COUNT(*),
        2
    ) as success_rate_percent
FROM pipeline_jobs
WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE);
"""

# ============================================================================
# 출력 (테스트용)
# ============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("Notion용 CI/CD DB 다이어그램 코드 모음")
    print("=" * 80)
    print()
    
    print("📊 1. Mermaid ERDiagram (온라인 렌더러용)")
    print("-" * 80)
    print(MERMAID_ER_DIAGRAM)
    print()
    
    print("📝 2. 텍스트 다이어그램")
    print("-" * 80)
    print(TEXT_DIAGRAM)
    print()
    
    print("📋 3. 테이블 구조 설명")
    print("-" * 80)
    print(TABLE_STRUCTURE)
    print()
    
    print("🔗 4. 엔티티 관계 설명")
    print("-" * 80)
    print(RELATIONSHIPS)
    print()
    
    print("✅ 5. 사용 방법")
    print("-" * 80)
    print(USAGE_INSTRUCTIONS)
    print()
    
    print("🔍 6. 샘플 쿼리")
    print("-" * 80)
    print(SAMPLE_QUERIES)
    print()
