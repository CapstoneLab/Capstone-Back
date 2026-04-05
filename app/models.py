from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class StartPipelineRequest(BaseModel):
    repo_url: HttpUrl
    branch: str = "main"
    trigger_source: str = "windows-api"


class StartPipelineResponse(BaseModel):
    job_id: str
    status: Literal["queued", "triggered"]
    message: str


class PipelineResultPayload(BaseModel):
    job_id: str
    status: Literal["success", "failed", "running"]
    repo_url: str
    branch: str = "main"
    started_at: datetime
    ended_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineResultResponse(BaseModel):
    found: bool
    data: PipelineResultPayload | None = None
    message: str
