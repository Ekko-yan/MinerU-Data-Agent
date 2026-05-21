from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "agent_data"
UPLOAD_ROOT = DATA_ROOT / "uploads"
JOB_ROOT = DATA_ROOT / "jobs"
DEFAULT_MODEL = "glm-4"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    upload_root: Path
    job_root: Path
    mineru_bin: Path
    default_backend: str
    default_method: str
    default_lang: str
    model_source: str
    openai_api_base: str | None
    openai_api_key: str | None
    openai_model: str
    llm_timeout_seconds: float
    job_timeout_seconds: float
    startup_timeout_seconds: float
    pre_enhance_timeout_seconds: float
    enable_image_preprocess: bool
    result_inline_chunks_limit: int
    chart_tool: str
    chart_vlm_api_base: str | None
    chart_vlm_api_key: str | None
    chart_vlm_model: str
    chart_vlm_timeout_seconds: float
    chart_vlm_max_candidates: int
    chart_vlm_max_image_mb: float
    chart_vlm_image_max_side: int
    chart_vlm_image_quality: int
    chart_vlm_verify_ssl: bool
    max_running_jobs: int

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_base and self.openai_api_key)


def load_settings() -> Settings:
    mineru_bin = Path(
        os.getenv(
            "MINERU_BIN",
            PROJECT_ROOT / ".venv_mineru" / "Scripts" / "mineru.exe",
        )
    )
    return Settings(
        project_root=PROJECT_ROOT,
        data_root=Path(os.getenv("DATA_AGENT_DATA_ROOT", DATA_ROOT)),
        upload_root=Path(os.getenv("DATA_AGENT_UPLOAD_ROOT", UPLOAD_ROOT)),
        job_root=Path(os.getenv("DATA_AGENT_JOB_ROOT", JOB_ROOT)),
        mineru_bin=mineru_bin,
        default_backend=os.getenv("DATA_AGENT_BACKEND", "pipeline"),
        default_method=os.getenv("DATA_AGENT_METHOD", "auto"),
        default_lang=os.getenv("DATA_AGENT_LANG", "ch"),
        model_source=os.getenv("MINERU_MODEL_SOURCE", "modelscope"),
        openai_api_base=os.getenv("OPENAI_API_BASE"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        llm_timeout_seconds=float(os.getenv("DATA_AGENT_LLM_TIMEOUT", "20")),
        job_timeout_seconds=float(os.getenv("DATA_AGENT_JOB_TIMEOUT_SECONDS", "1800")),
        startup_timeout_seconds=float(os.getenv("DATA_AGENT_STARTUP_TIMEOUT_SECONDS", "180")),
        pre_enhance_timeout_seconds=float(os.getenv("DATA_AGENT_PRE_ENHANCE_TIMEOUT_SECONDS", "15")),
        enable_image_preprocess=os.getenv("DATA_AGENT_ENABLE_IMAGE_PREPROCESS", "false").lower()
        in {"1", "true", "yes", "on"},
        result_inline_chunks_limit=int(os.getenv("DATA_AGENT_RESULT_INLINE_CHUNKS_LIMIT", "10000")),
        chart_tool=os.getenv("DATA_AGENT_CHART_TOOL", "").strip().lower(),
        chart_vlm_api_base=os.getenv("DATA_AGENT_CHART_VLM_API_BASE") or os.getenv("OPENAI_API_BASE"),
        chart_vlm_api_key=os.getenv("DATA_AGENT_CHART_VLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
        chart_vlm_model=os.getenv("DATA_AGENT_CHART_VLM_MODEL", "qwen3-vl-plus"),
        chart_vlm_timeout_seconds=float(os.getenv("DATA_AGENT_CHART_VLM_TIMEOUT", "30")),
        chart_vlm_max_candidates=int(os.getenv("DATA_AGENT_CHART_VLM_MAX_CANDIDATES", "3")),
        chart_vlm_max_image_mb=float(os.getenv("DATA_AGENT_CHART_VLM_MAX_IMAGE_MB", "6")),
        chart_vlm_image_max_side=int(os.getenv("DATA_AGENT_CHART_VLM_IMAGE_MAX_SIDE", "1600")),
        chart_vlm_image_quality=int(os.getenv("DATA_AGENT_CHART_VLM_IMAGE_QUALITY", "85")),
        chart_vlm_verify_ssl=os.getenv("DATA_AGENT_CHART_VLM_VERIFY_SSL", "true").lower()
        in {"1", "true", "yes", "on"},
        max_running_jobs=int(os.getenv("DATA_AGENT_MAX_RUNNING_JOBS", "1")),
    )


settings = load_settings()

for directory in (settings.data_root, settings.upload_root, settings.job_root):
    directory.mkdir(parents=True, exist_ok=True)
