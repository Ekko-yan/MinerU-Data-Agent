from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class AgentLog(BaseModel):
    time: datetime
    stage: str
    level: Literal["info", "warning", "error"] = "info"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class JobOptions(BaseModel):
    backend: str = "pipeline"
    method: str = "auto"
    lang: str = "ch"
    formula: bool = True
    table: bool = True
    image_analysis: bool = True
    model_source: str = "modelscope"
    use_llm: bool = True
    api_url: str | None = None
    server_url: str | None = None
    start_page: int = 0
    end_page: int | None = None


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    input_filename: str
    input_path: str
    output_dir: str
    options: JobOptions
    logs: list[AgentLog] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class CreateJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    result_url: str


class DeleteJobResponse(BaseModel):
    job_id: str
    deleted: bool
    cancelled_process: bool


class ProgressResponse(BaseModel):
    job_id: str
    status: JobStatus | Literal["orphaned"]
    stage: str
    message: str
    percent: float | None = None
    current: int | None = None
    total: int | None = None
    running_seconds: float | None = None
    cli_tail: str = ""


class HealthResponse(BaseModel):
    status: str
    service: str
    mineru_available: bool
    llm_configured: bool
    llm_model: str
    chart_tool: str
    chart_vlm_configured: bool
    chart_vlm_model: str
    supported_inputs: list[str]
