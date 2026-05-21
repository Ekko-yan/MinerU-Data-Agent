from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from backend.agent_runner import run_job
from backend.config import settings
from backend.process_registry import cancel_process, has_process
from backend.schemas import (
    CreateJobResponse,
    DeleteJobResponse,
    HealthResponse,
    JobOptions,
    JobRecord,
    ProgressResponse,
)
from backend.storage import append_log, create_job_record, delete_job, job_dir, list_jobs, load_job, update_job
from process_documents_with_mineru import SUPPORTED_INPUT_SUFFIXES


app = FastAPI(
    title="MinerU Data Agent",
    description="A modular Data Agent for document understanding, parsing, planning and verification.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_job(job_id: str) -> JobRecord:
    try:
        return load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


def _safe_upload_name(filename: str) -> str:
    name = Path(filename or "document").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_INPUT_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_INPUT_SUFFIXES))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件后缀 {suffix!r}。支持类型：{supported}",
        )
    return name


def _safe_job_workspace(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", job_id):
        raise HTTPException(status_code=400, detail="任务 ID 不合法")
    root = settings.job_root.resolve()
    workspace = (root / job_id).resolve()
    if workspace.parent != root:
        raise HTTPException(status_code=400, detail="任务 ID 不合法")
    return workspace


def _output_dir_for_job(job_id: str) -> Path:
    try:
        return Path(load_job(job_id).output_dir)
    except FileNotFoundError:
        return _safe_job_workspace(job_id) / "output"


def _read_text_tail(path: Path, tail: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail > 0 and len(text) > tail:
        return text[-tail:]
    return text


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _lightweight_record(record: JobRecord) -> JobRecord:
    if not record.result or "chunks" not in record.result:
        return record
    light_result = dict(record.result)
    light_result.pop("chunks", None)
    record.result = light_result
    return record


def _infer_cli_progress(cli_text: str) -> tuple[str | None, float | None, int | None, int | None, str | None]:
    lines = [line.strip() for line in cli_text.splitlines() if line.strip()]
    current = None
    total = None
    stage = None
    message = lines[-1] if lines else None

    for line in reversed(lines):
        match = re.search(r"(?P<stage>[A-Za-z][A-Za-z /_-]+):\s*\d+%.*?\|\s*(?P<current>\d+)/(?P<total>\d+)", line)
        if match:
            stage = match.group("stage").strip()
            current = int(match.group("current"))
            total = int(match.group("total"))
            message = line
            break
        match = re.search(r"Processed\s+(?P<current>\d+)/(?P<total>\d+)\s+page", line)
        if match:
            stage = "pages"
            current = int(match.group("current"))
            total = int(match.group("total"))
            message = line
            break

    if total is None:
        for line in reversed(lines):
            match = re.search(r"(?P<total>\d+)\s+page total", line)
            if match:
                total = int(match.group("total"))
                current = 0
                stage = stage or "pages"
                break

    exit_match = re.search(r"mineru_exit_code=(?P<code>-?\d+)", cli_text)
    if exit_match:
        code = int(exit_match.group("code"))
        stage = "finished" if code == 0 else "failed"
        if code == 0:
            current = total or current
        message = f"MinerU exited with code {code}."

    percent = None
    if total and current is not None:
        percent = max(0.0, min(100.0, round((current / total) * 100, 1)))
    return stage, percent, current, total, message


def _running_seconds(record: JobRecord) -> float:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if record.status in {"succeeded", "failed", "cancelled"}:
        updated_at = record.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return max(0.0, (updated_at - created_at).total_seconds())
    return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())


def _mark_stale_running_job(record: JobRecord, cli_tail: str) -> JobRecord:
    if record.status not in {"queued", "running"}:
        return record
    if "mineru_exit_code=0" in cli_tail:
        return record

    elapsed = _running_seconds(record)
    output_dir = Path(record.output_dir)
    cli_path = output_dir / "mineru_cli.log"
    has_cli_log = cli_path.exists()
    stale_log_seconds = None
    if has_cli_log:
        stale_log_seconds = max(0.0, (datetime.now() - datetime.fromtimestamp(cli_path.stat().st_mtime)).total_seconds())

    missing_process = record.status == "running" and has_cli_log and not has_process(record.job_id)
    timed_out = settings.job_timeout_seconds > 0 and elapsed > settings.job_timeout_seconds
    stale_output = (
        record.status == "running"
        and has_cli_log
        and stale_log_seconds is not None
        and stale_log_seconds > 300
        and not has_process(record.job_id)
    )
    startup_stalled = (
        record.status == "running"
        and not has_cli_log
        and settings.startup_timeout_seconds > 0
        and elapsed > settings.startup_timeout_seconds
        and not has_process(record.job_id)
    )
    if not (missing_process or timed_out or stale_output or startup_stalled):
        return record

    cancel_process(record.job_id)
    reason = "MinerU 进程在任务完成前消失。"
    if timed_out:
        reason = f"MinerU 超过 {settings.job_timeout_seconds:.0f} 秒超时上限。"
    elif startup_stalled:
        reason = f"任务启动超过 {settings.startup_timeout_seconds:.0f} 秒仍未产生 MinerU 命令行日志，且没有找到存活 MinerU 进程。"
    elif stale_output:
        reason = "MinerU 输出长时间未更新，且没有找到对应存活进程。"
    if "mineru_exit_code=" in cli_tail:
        exit_code = cli_tail.rsplit("mineru_exit_code=", 1)[-1].splitlines()[0].strip()
        reason = f"MinerU 执行失败，退出码 {exit_code}。"

    try:
        append_log(record.job_id, "error", reason, level="error")
        return update_job(record.job_id, status="failed", error=reason)
    except FileNotFoundError:
        return record


def _run_job_background(job_id: str) -> None:
    asyncio.run(run_job(job_id))


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="mineru-data-agent",
        mineru_available=settings.mineru_bin.exists(),
        llm_configured=settings.llm_enabled,
        llm_model=settings.openai_model,
        chart_tool=settings.chart_tool or "not_configured",
        chart_vlm_configured=bool(settings.chart_vlm_api_base and settings.chart_vlm_api_key),
        chart_vlm_model=settings.chart_vlm_model,
        supported_inputs=sorted(SUPPORTED_INPUT_SUFFIXES),
    )


@app.post("/api/parse", response_model=CreateJobResponse)
@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    backend: str = Form(settings.default_backend),
    method: str = Form(settings.default_method),
    lang: str = Form(settings.default_lang),
    formula: bool = Form(True),
    table: bool = Form(True),
    image_analysis: bool = Form(True),
    model_source: str = Form(settings.model_source),
    use_llm: bool = Form(True),
    api_url: str | None = Form(None),
    server_url: str | None = Form(None),
    start_page: int = Form(0),
    end_page: int | None = Form(None),
) -> CreateJobResponse:
    upload_name = _safe_upload_name(file.filename or "document")
    job_id = uuid.uuid4().hex
    workspace = job_dir(job_id)
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / upload_name

    with input_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    options = JobOptions(
        backend=backend,
        method=method,
        lang=lang,
        formula=formula,
        table=table,
        image_analysis=image_analysis,
        model_source=model_source,
        use_llm=use_llm,
        api_url=api_url,
        server_url=server_url,
        start_page=start_page,
        end_page=end_page,
    )
    create_job_record(job_id, upload_name, input_path, output_dir, options)
    append_log(job_id, "queue", "任务已进入队列，等待可用的 MinerU worker。")
    background_tasks.add_task(_run_job_background, job_id)

    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/jobs/{job_id}",
        result_url=f"/api/jobs/{job_id}/result",
    )


@app.get("/api/jobs", response_model=list[JobRecord])
def jobs() -> list[JobRecord]:
    records: list[JobRecord] = []
    for record in list_jobs():
        cli_tail = _read_text_tail(Path(record.output_dir) / "mineru_cli.log", 4000)
        records.append(_lightweight_record(_mark_stale_running_job(record, cli_tail)))
    return records


@app.get("/api/jobs/{job_id}", response_model=JobRecord)
def job_status(job_id: str) -> JobRecord:
    record = _require_job(job_id)
    cli_tail = _read_text_tail(Path(record.output_dir) / "mineru_cli.log", 4000)
    return _lightweight_record(_mark_stale_running_job(record, cli_tail))


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str) -> dict:
    record = _require_job(job_id)
    if record.status == "failed":
        raise HTTPException(status_code=500, detail=record.error or "任务执行失败")
    if record.status != "succeeded" or record.result is None:
        raise HTTPException(status_code=202, detail="任务尚未完成")
    result_path = Path(record.output_dir) / "agent_result.json"
    if result_path.exists():
        return _read_json(result_path)
    return record.result


@app.get("/api/jobs/{job_id}/cli-log", response_class=PlainTextResponse)
def job_cli_log(job_id: str, tail: int = 20000) -> str:
    path = _output_dir_for_job(job_id) / "mineru_cli.log"
    if not path.exists():
        return "命令行日志尚未生成。任务可能还在排队、规划或启动 MinerU。"
    return _read_text_tail(path, tail)


@app.get("/api/jobs/{job_id}/progress", response_model=ProgressResponse)
def job_progress(job_id: str, tail: int = 12000) -> ProgressResponse:
    output_dir = _output_dir_for_job(job_id)
    cli_tail = _read_text_tail(output_dir / "mineru_cli.log", tail)
    cli_stage, percent, current, total, cli_message = _infer_cli_progress(cli_tail)

    try:
        record = load_job(job_id)
    except FileNotFoundError:
        if not output_dir.parent.exists():
            raise HTTPException(status_code=404, detail="任务不存在")
        return ProgressResponse(
            job_id=job_id,
            status="orphaned",
            stage=cli_stage or "orphaned",
            message=cli_message or "任务记录已不存在，但输出目录仍在。建议删除该任务以清理残留进程和文件。",
            percent=percent,
            current=current,
            total=total,
            running_seconds=None,
            cli_tail=cli_tail,
        )

    last_log = record.logs[-1] if record.logs else None
    record = _mark_stale_running_job(record, cli_tail)
    last_log = record.logs[-1] if record.logs else None
    stage = cli_stage or (last_log.stage if last_log else record.status)
    message = cli_message or (last_log.message if last_log else "任务已创建。")
    if record.status == "queued":
        stage = "queue"
        message = "任务正在排队，等待当前 MinerU 任务结束。"
        percent = percent if percent is not None else 0.0
    elif record.status == "running" and not cli_tail:
        stage = "execute"
        message = "MinerU 子进程已启动，正在加载模型或等待首行命令行输出。"
    elif record.status == "succeeded":
        percent = 100.0
        stage = "finished"
        message = "任务已完成，结构化结果已生成。"
    elif record.status == "failed":
        stage = "failed"
        message = record.error or (last_log.message if last_log else "任务执行失败。")
    elif record.status == "cancelled":
        stage = "cancelled"
        message = record.error or "任务已取消。"

    return ProgressResponse(
        job_id=job_id,
        status=record.status,
        stage=stage,
        message=message,
        percent=percent,
        current=current,
        total=total,
        running_seconds=_running_seconds(record),
        cli_tail=cli_tail,
    )


@app.delete("/api/jobs/{job_id}", response_model=DeleteJobResponse)
def remove_job(job_id: str) -> DeleteJobResponse:
    cancelled_process = cancel_process(job_id)
    deleted = delete_job(job_id)
    if not deleted and not cancelled_process:
        raise HTTPException(status_code=404, detail="任务不存在")
    return DeleteJobResponse(
        job_id=job_id,
        deleted=deleted,
        cancelled_process=cancelled_process,
    )


@app.get("/api/jobs/{job_id}/artifact/{name}")
def job_artifact(job_id: str, name: str) -> FileResponse:
    record = _require_job(job_id)
    allowed = {
        "manifest": Path(record.output_dir) / "structured_manifest.json",
        "chunks": Path(record.output_dir) / "structured_chunks.jsonl",
        "agent_result": Path(record.output_dir) / "agent_result.json",
        "enhancement_report": Path(record.output_dir) / "enhancement_report.json",
        "cli_log": Path(record.output_dir) / "mineru_cli.log",
    }
    path = allowed.get(name)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    if path.suffix == ".json":
        media_type = "application/json"
    elif path.suffix == ".jsonl":
        media_type = "application/x-ndjson"
    else:
        media_type = "text/plain"
    return FileResponse(path, media_type=media_type, filename=path.name)
