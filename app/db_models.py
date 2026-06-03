from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from sqlalchemy.orm import Mapped, mapped_column

# PostgreSQL enum types matching the DB schema
JobStatus = Enum('queued', 'running', 'success', 'failed', 'cancelled', name='job_status', create_type=False)
StepStatus = Enum('pending', 'running', 'success', 'failed', 'skipped', name='step_status', create_type=False)
ScanType = Enum('gitleaks', 'semgrep', name='scan_type', create_type=False)
SeverityLevel = Enum('critical', 'high', 'medium', 'low', name='severity_level', create_type=False)
ArtifactType = Enum('jar', 'docker', 'wheel', 'zip', 'tar', 'tar.gz', 'other', name='artifact_type', create_type=False)
DeploymentStatus = Enum('pending', 'in_progress', 'success', 'failed', 'rollback', name='deployment_status', create_type=False)
Environment = Enum('dev', 'staging', 'production', name='environment', create_type=False)

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )
    github_login: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(150))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(200))
    github_access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class PipelineJob(Base):
    """pipeline_jobs 테이블 — RDS에 이미 존재하는 테이블과 매핑."""
    __tablename__ = "pipeline_jobs"
    __table_args__ = {"extend_existing": True}

    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    repo_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    trigger_source: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(JobStatus, nullable=False, default="queued")
    overall_result: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_secs: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    # 엔진 polling 지원 필드
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="capstone")
    environment: Mapped[str] = mapped_column(String(50), nullable=False, default="development")
    workflow_path: Mapped[str | None] = mapped_column(String(2048))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    selected_items: Mapped[list | None] = mapped_column(JSONB, default=list)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    approved_cwes: Mapped[list | None] = mapped_column(JSONB, default=list)
    approval_record_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))


class PipelineStep(Base):
    """pipeline_steps 테이블."""
    __tablename__ = "pipeline_steps"
    __table_args__ = {"extend_existing": True}

    step_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(StepStatus, nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_secs: Mapped[float | None] = mapped_column(Float)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class StepLog(Base):
    """step_logs 테이블."""
    __tablename__ = "step_logs"
    __table_args__ = {"extend_existing": True}

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_steps.step_id", ondelete="CASCADE"), nullable=False)
    log_level: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    log_content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class SecurityFinding(Base):
    """security_findings 테이블."""
    __tablename__ = "security_findings"
    __table_args__ = {"extend_existing": True}

    finding_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_steps.step_id", ondelete="CASCADE"), nullable=False)
    scan_type: Mapped[str] = mapped_column(ScanType, nullable=False)
    severity: Mapped[str] = mapped_column(SeverityLevel, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_name: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    column_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    code_snippet: Mapped[str | None] = mapped_column(Text)
    cwe_id: Mapped[str | None] = mapped_column(String(20))
    cvss_score: Mapped[float | None] = mapped_column(Float)
    is_masked: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_fix_suggestion: Mapped[str | None] = mapped_column(Text)
    policy_item: Mapped[str | None] = mapped_column(String(100))
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class BuildArtifact(Base):
    """build_artifacts 테이블."""
    __tablename__ = "build_artifacts"
    __table_args__ = {"extend_existing": True}

    artifact_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_steps.step_id", ondelete="CASCADE"), nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_type: Mapped[str] = mapped_column(ArtifactType, nullable=False)
    location: Mapped[str] = mapped_column(String(2048), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    checksum_algorithm: Mapped[str | None] = mapped_column(String(20), default="sha256")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class Deployment(Base):
    """deployments 테이블."""
    __tablename__ = "deployments"
    __table_args__ = {"extend_existing": True}

    deployment_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    artifact_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("build_artifacts.artifact_id", ondelete="CASCADE"), nullable=False)
    target_env: Mapped[str] = mapped_column(Environment, nullable=False)
    deployment_status: Mapped[str] = mapped_column(DeploymentStatus, nullable=False, default="pending")
    deployed_by: Mapped[str | None] = mapped_column(String(255))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deployment_result: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class SecuritySummary(Base):
    """security_summary 테이블."""
    __tablename__ = "security_summary"
    __table_args__ = {"extend_existing": True}

    summary_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False, unique=True)
    total_findings: Mapped[int] = mapped_column(Integer, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    high_count: Mapped[int] = mapped_column(Integer, default=0)
    medium_count: Mapped[int] = mapped_column(Integer, default=0)
    low_count: Mapped[int] = mapped_column(Integer, default=0)
    gitleaks_count: Mapped[int] = mapped_column(Integer, default=0)
    semgrep_count: Mapped[int] = mapped_column(Integer, default=0)
    overall_status: Mapped[str] = mapped_column(String(20), default="passed")
    status_reason: Mapped[str | None] = mapped_column(Text)
    # 엔진 verdict 스냅샷 (새 스키마)
    verdict: Mapped[str | None] = mapped_column(String(50))
    score: Mapped[float | None] = mapped_column(Float)
    score_label: Mapped[str | None] = mapped_column(String(200))
    gauge_color: Mapped[str | None] = mapped_column(String(20))
    selected_items: Mapped[list | None] = mapped_column(JSONB, default=list)
    selected_count: Mapped[int | None] = mapped_column(Integer)
    out_of_scope_count: Mapped[int | None] = mapped_column(Integer, default=0)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reasons: Mapped[list | None] = mapped_column(JSONB, default=list)
    warn_reasons: Mapped[list | None] = mapped_column(JSONB, default=list)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    scanned_commit_sha: Mapped[str | None] = mapped_column(String(64))
    acknowledged_cwes: Mapped[list | None] = mapped_column(JSONB, default=list)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


ApprovalStatus = Enum('pending', 'approved', 'rejected', name='approval_status', create_type=False)


class ApprovalRecord(Base):
    """approval_records 테이블 — High 승인 예외 워크플로."""
    __tablename__ = "approval_records"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    repo: Mapped[str] = mapped_column(String(2048), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_cwes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    block_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    verdict_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    approver_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(ApprovalStatus, nullable=False, default="pending")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned_commit_sha: Mapped[str | None] = mapped_column(String(64))
    acknowledged_cwes: Mapped[list | None] = mapped_column(JSONB, default=list)
    followup_job_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
