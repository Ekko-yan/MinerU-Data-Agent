from __future__ import annotations

import asyncio
import json
import locale
import os
import queue
import subprocess
import time
from pathlib import Path
from threading import BoundedSemaphore, Thread
from typing import Any

from backend.config import settings
from backend.enhancements import (
    build_pre_enhancement_timeout_report,
    run_post_mineru_enhancements,
    run_pre_mineru_enhancements,
    write_enhancement_report,
)
from backend.llm_client import build_processing_plan, build_quality_report
from backend.process_registry import cancel_process, register_process, unregister_process
from backend.schemas import JobRecord
from backend.storage import append_log, load_job, update_job
from process_documents_with_mineru import build_structured_outputs

_job_semaphore = BoundedSemaphore(settings.max_running_jobs)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _build_mineru_command(record: JobRecord) -> list[str]:
    options = record.options
    input_path = record.input_path
    if record.result and isinstance(record.result.get("effective_input_path"), str):
        input_path = record.result["effective_input_path"]
    command = [
        str(settings.mineru_bin),
        "-p",
        input_path,
        "-o",
        record.output_dir,
        "-b",
        options.backend,
        "-m",
        options.method,
        "-l",
        options.lang,
        "-f",
        _bool_text(options.formula),
        "-t",
        _bool_text(options.table),
        "--image-analysis",
        _bool_text(options.image_analysis),
        "--start",
        str(options.start_page),
    ]
    if options.end_page is not None:
        command.extend(["--end", str(options.end_page)])
    if options.api_url:
        command.extend(["--api-url", options.api_url])
    if options.server_url:
        command.extend(["--url", options.server_url])
    return command


def _fallback_plan(filename: str) -> dict[str, Any]:
    return {
        "goal": f"将 {filename} 解析为 Markdown，并生成规范化的结构化 JSON 片段。",
        "steps": [
            "校验输入类型，并创建独立任务工作区。",
            "按所选后端和解析参数调用 MinerU。",
            "收集 Markdown、content_list、middle_json、图片和元数据。",
            "将解析元素规范化为 JSONL 结构化片段。",
            "检查输出完整性，并生成可复现的处理日志。",
        ],
        "risk_controls": [
            "保留 MinerU 原始输出，便于审计和复现。",
            "返回文件路径和元素统计，便于下游校验。",
            "在任务日志中暴露解析警告和异常信息。",
        ],
    }


def _fallback_quality_report(manifest: dict[str, Any]) -> dict[str, Any]:
    document_count = int(manifest.get("summary", {}).get("documents", 0) or 0)
    chunk_count = int(manifest.get("summary", {}).get("chunks", 0) or 0)
    return {
        "verdict": "通过" if document_count and chunk_count else "需复核",
        "checks": [
            {
                "name": "文档数量",
                "status": "通过" if document_count else "警告",
                "message": f"检测到 {document_count} 个已解析文档。",
            },
            {
                "name": "结构片段数量",
                "status": "通过" if chunk_count else "警告",
                "message": f"生成了 {chunk_count} 个结构化片段。",
            },
        ],
        "suggestions": [],
    }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and len(rows) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _read_jsonl_preview(path: Path, limit: int = 8) -> list[dict[str, Any]]:
    return _read_jsonl(path, limit)


def _write_result_file(record: JobRecord, result: dict[str, Any]) -> None:
    result_path = Path(record.output_dir) / "agent_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def cli_log_path(record: JobRecord) -> Path:
    return Path(record.output_dir) / "mineru_cli.log"


def _decode_process_output(raw_line: str | bytes) -> str:
    if isinstance(raw_line, str):
        return raw_line

    encodings = [
        "utf-8",
        locale.getpreferredencoding(False),
        "gb18030",
        "gbk",
    ]
    for encoding in dict.fromkeys(encodings):
        try:
            return raw_line.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_line.decode("utf-8", errors="replace")


def _stream_process_output(stream: Any, line_queue: queue.Queue[str | None]) -> None:
    try:
        for raw_line in stream:
            line_queue.put(_decode_process_output(raw_line))
    finally:
        line_queue.put(None)


def _run_mineru_process(
    job_id: str,
    command: list[str],
    env: dict[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    record = load_job(job_id)
    log_path = cli_log_path(record)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    captured_output: list[str] = []
    started_at = time.monotonic()

    process = subprocess.Popen(
        command,
        env=env,
        cwd=str(settings.project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    register_process(job_id, process)
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write("$ " + " ".join(command) + "\n")
            log_file.write(f"[data-agent] cwd={settings.project_root}\n")
            log_file.write(f"[data-agent] mineru_pid={process.pid}\n\n")
            log_file.flush()

            assert process.stdout is not None
            line_queue: queue.Queue[str | None] = queue.Queue()
            reader = Thread(
                target=_stream_process_output,
                args=(process.stdout, line_queue),
                daemon=True,
            )
            reader.start()
            stream_finished = False

            while True:
                try:
                    line = line_queue.get(timeout=0.2)
                except queue.Empty:
                    line = ""

                if line is None:
                    stream_finished = True
                elif line:
                    captured_output.append(line)
                    log_file.write(line)
                    log_file.flush()

                if process.poll() is not None and stream_finished and line_queue.empty():
                    break

                elapsed = time.monotonic() - started_at
                if timeout_seconds > 0 and elapsed > timeout_seconds:
                    message = (
                        f"\n[data-agent] timeout_after_seconds={timeout_seconds:.0f}; "
                        "正在终止 MinerU 进程树\n"
                    )
                    captured_output.append(message)
                    log_file.write(message)
                    log_file.flush()
                    cancel_process(job_id)
                    try:
                        return_code = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        return_code = process.poll()
                    log_file.write(f"\n[data-agent] mineru_exit_code={return_code}\n")
                    log_file.flush()
                    raise TimeoutError(f"MinerU 超过 {timeout_seconds:.0f} 秒超时上限")

            return_code = process.wait()
            while not line_queue.empty():
                line = line_queue.get_nowait()
                if line is None:
                    continue
                captured_output.append(line)
                log_file.write(line)
                log_file.flush()
            log_file.write(f"\n[data-agent] mineru_exit_code={return_code}\n")
            log_file.flush()
        return subprocess.CompletedProcess(command, process.returncode, stdout="".join(captured_output))
    finally:
        unregister_process(job_id)


async def run_job(job_id: str) -> None:
    await asyncio.to_thread(_job_semaphore.acquire)
    try:
        await _run_job_inner(job_id)
    finally:
        _job_semaphore.release()


async def _call_with_timeout(coro: Any, timeout_seconds: float) -> tuple[Any, str | None]:
    try:
        if timeout_seconds > 0:
            return await asyncio.wait_for(coro, timeout=timeout_seconds), None
        return await coro, None
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"


def _pop_llm_trace(payload: dict[str, Any]) -> dict[str, Any] | None:
    trace = payload.pop("_llm_fallback", None)
    return trace if isinstance(trace, dict) else None


async def _run_job_inner(job_id: str) -> None:
    try:
        record = load_job(job_id)
    except FileNotFoundError:
        return
    try:
        update_job(job_id, status="running")
        append_log(job_id, "understand", "任务已接收，输入校验完成。", details={
            "filename": record.input_filename,
            "input_path": record.input_path,
        })

        append_log(job_id, "enhance", "正在运行 MinerU 前置增强模块。")
        try:
            pre_enhancement_report = await asyncio.wait_for(
                asyncio.to_thread(run_pre_mineru_enhancements, record),
                timeout=settings.pre_enhance_timeout_seconds,
            )
        except TimeoutError:
            pre_enhancement_report = build_pre_enhancement_timeout_report(
                record,
                settings.pre_enhance_timeout_seconds,
            )
        effective_input_path = pre_enhancement_report.get("effective_input_path", record.input_path)
        update_job(job_id, result={"effective_input_path": effective_input_path})
        append_log(job_id, "enhance", "前置增强模块已完成。", details={
            "effective_input_path": effective_input_path,
            "modules": pre_enhancement_report.get("modules", []),
        })

        plan = None
        if record.options.use_llm:
            append_log(job_id, "plan", "正在调用 LLM 生成执行计划。")
            plan, plan_error = await _call_with_timeout(
                build_processing_plan(
                    record.input_filename,
                    record.options.model_dump(mode="json"),
                ),
                settings.llm_timeout_seconds + 5,
            )
            if plan_error or not isinstance(plan, dict):
                append_log(job_id, "plan", "LLM 规划调用失败，已降级为本地执行计划。", level="warning", details={
                    "error": plan_error or "LLM 返回内容不是 JSON 对象。",
                })
                plan = _fallback_plan(record.input_filename)
            else:
                llm_trace = _pop_llm_trace(plan)
                if llm_trace:
                    append_log(job_id, "plan", "LLM 规划调用失败，已降级为本地执行计划。", level="warning", details=llm_trace)
        else:
            plan = _fallback_plan(record.input_filename)
        append_log(job_id, "plan", "执行计划已生成。", details={"plan": plan})

        command = _build_mineru_command(load_job(job_id))
        env = os.environ.copy()
        env["MINERU_MODEL_SOURCE"] = record.options.model_source
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        append_log(job_id, "execute", "正在调用 MinerU 结构化解析模块。", details={
            "command": command,
        })

        completed = await asyncio.to_thread(
            _run_mineru_process,
            job_id,
            command,
            env,
            settings.job_timeout_seconds,
        )
        output_tail = (completed.stdout or "")[-5000:]
        append_log(job_id, "execute", "MinerU 执行已结束。", details={
            "returncode": completed.returncode,
            "output_tail": output_tail,
        })
        if completed.returncode != 0:
            raise RuntimeError(f"MinerU 执行失败，退出码 {completed.returncode}")

        append_log(job_id, "structure", "正在汇总 Markdown、JSON 和元素级 JSONL 输出。")
        fake_args = type(
            "Args",
            (),
            {
                "input": Path(record.input_path),
                "backend": record.options.backend,
                "method": record.options.method,
                "lang": record.options.lang,
            },
        )()
        manifest_path, chunks_path = build_structured_outputs(
            Path(record.output_dir),
            command,
            fake_args,
        )
        manifest = _read_json(manifest_path)
        chunks = _read_jsonl(chunks_path, settings.result_inline_chunks_limit)
        chunks_preview = _read_jsonl_preview(chunks_path)
        inline_chunks_truncated = len(chunks) < int(manifest.get("summary", {}).get("chunks", 0) or 0)
        append_log(job_id, "structure", "结构化输出已生成。", details={
            "manifest_path": str(manifest_path),
            "chunks_path": str(chunks_path),
            "inline_chunks": len(chunks),
            "inline_chunks_truncated": inline_chunks_truncated,
            "summary": manifest.get("summary", {}),
        })

        append_log(job_id, "enhance", "正在运行 MinerU 后置增强模块。")
        post_enhancement_report = await asyncio.to_thread(
            run_post_mineru_enhancements,
            load_job(job_id),
            manifest,
            chunks_path,
        )
        enhancement_report, enhancement_report_path = await asyncio.to_thread(
            write_enhancement_report,
            load_job(job_id),
            pre_enhancement_report,
            post_enhancement_report,
        )
        append_log(job_id, "enhance", "后置增强模块已完成。", details={
            "enhancement_report_path": str(enhancement_report_path),
            "modules": post_enhancement_report.get("modules", []),
        })

        quality_report = None
        if record.options.use_llm:
            append_log(job_id, "verify", "正在调用 LLM 进行质量检查。")
            quality_report, quality_error = await _call_with_timeout(
                build_quality_report(manifest, chunks_preview),
                settings.llm_timeout_seconds + 5,
            )
            if quality_error or not isinstance(quality_report, dict):
                append_log(job_id, "verify", "LLM 质量检查调用失败，已降级为本地检查。", level="warning", details={
                    "error": quality_error or "LLM 返回内容不是 JSON 对象。",
                })
                quality_report = _fallback_quality_report(manifest)
            else:
                llm_trace = _pop_llm_trace(quality_report)
                if llm_trace:
                    append_log(job_id, "verify", "LLM 质量检查调用失败，已降级为本地检查。", level="warning", details=llm_trace)
        else:
            quality_report = _fallback_quality_report(manifest)
        append_log(job_id, "verify", "质量检查已完成。", details={
            "quality_report": quality_report,
        })

        result = {
            "job_id": job_id,
            "status": "succeeded",
            "plan": plan,
            "quality_report": quality_report,
            "effective_input_path": effective_input_path,
            "manifest_path": str(manifest_path),
            "chunks_path": str(chunks_path),
            "enhancement_report_path": str(enhancement_report_path),
            "manifest": manifest,
            "chunks": chunks,
            "chunks_preview": chunks_preview,
            "chunks_inline_truncated": inline_chunks_truncated,
            "enhancement_report": enhancement_report,
        }
        _write_result_file(record, result)
        lightweight_result = dict(result)
        lightweight_result.pop("chunks", None)
        update_job(job_id, status="succeeded", result=lightweight_result)
    except Exception as exc:
        try:
            append_log(job_id, "error", str(exc), level="error")
            update_job(job_id, status="failed", error=str(exc))
        except FileNotFoundError:
            return
