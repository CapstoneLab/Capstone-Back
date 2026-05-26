from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class StartPipelineRequest(BaseModel):
    repo_url: HttpUrl
    branch: str = "main"
    trigger_source: Literal["manual", "webhook", "windows-api"] = "windows-api"
    env_vars: dict[str, str] = Field(default_factory=dict)
    selected_checks: list[str] = Field(
        default_factory=list,
        description="실행할 검사 항목 (예: ['build','lightweight-security','deep-security','deploy']). 비어 있으면 전체 실행.",
    )
    is_first_run: bool = Field(
        default=False,
        description="최초 실행 여부 플래그 (엔진에 IS_FIRST_RUN 환경변수로 전달).",
    )
    source: str = Field(default="capstone", description="엔진 소스 식별자 (capstone | mirae)")
    environment: str = Field(default="development", description="실행 환경 (production | staging | development | feature)")
    workflow_path: str | None = Field(default=None, description="커스텀 워크플로우 파일 경로")


class SecurityPayload(BaseModel):
    """엔진이 콜백으로 보내는 security 블록."""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summaries: list[dict[str, Any]] = Field(default_factory=list)
    verdict: dict[str, Any] = Field(default_factory=dict)


class StartPipelineResponse(BaseModel):
    job_id: str
    status: Literal["queued", "triggered"]
    message: str


class PipelineResultPayload(BaseModel):
    job_id: str
    # Engine sends "status" for pipeline_complete, "pipeline_status" for step_complete
    status: Literal["success", "failed", "running"] | None = None
    pipeline_status: Literal["success", "failed", "running"] | None = None
    repo_url: str
    branch: str = "main"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    security: dict[str, Any] = Field(default_factory=dict)
    # Engine sends these at top level for step_complete callbacks
    type: str | None = None
    step: dict[str, Any] = Field(default_factory=dict)

    @property
    def effective_status(self) -> str:
        return self.status or self.pipeline_status or "running"

    @property
    def callback_type(self) -> str:
        return self.type or (self.metadata.get("type") if self.metadata else None) or "pipeline_complete"


class PipelineResultResponse(BaseModel):
    found: bool
    data: PipelineResultPayload | None = None
    message: str
