from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from backend.config import settings
from backend.schemas import AgentLog, JobOptions, JobRecord, JobStatus


_lock = Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def job_dir(job_id: str) -> Path:
    return settings.job_root / job_id


def job_record_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    os.replace(temp_path, path)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def create_job_record(
    job_id: str,
    input_filename: str,
    input_path: Path,
    output_dir: Path,
    options: JobOptions,
) -> JobRecord:
    now = utc_now()
    record = JobRecord(
        job_id=job_id,
        status="queued",
        created_at=now,
        updated_at=now,
        input_filename=input_filename,
        input_path=str(input_path),
        output_dir=str(output_dir),
        options=options,
        logs=[],
    )
    save_job(record)
    return record


def save_job(record: JobRecord) -> None:
    with _lock:
        save_json(job_record_path(record.job_id), record.model_dump(mode="json"))


def load_job(job_id: str) -> JobRecord:
    path = job_record_path(job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    with _lock:
        return JobRecord.model_validate(load_json(path))


def list_jobs() -> list[JobRecord]:
    records = []
    with _lock:
        for path in sorted(settings.job_root.glob("*/job.json"), reverse=True):
            try:
                records.append(JobRecord.model_validate(load_json(path)))
            except Exception:
                continue
    return records


def delete_job(job_id: str) -> bool:
    directory = job_dir(job_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> JobRecord:
    record = load_job(job_id)
    if status is not None:
        record.status = status
    if result is not None:
        record.result = result
        record.error = None
    if error is not None:
        record.error = error
    record.updated_at = utc_now()
    save_job(record)
    return record


def append_log(
    job_id: str,
    stage: str,
    message: str,
    *,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> JobRecord:
    record = load_job(job_id)
    record.logs.append(
        AgentLog(
            time=utc_now(),
            stage=stage,
            level=level,  # type: ignore[arg-type]
            message=message,
            details=details or {},
        )
    )
    record.updated_at = utc_now()
    save_job(record)
    return record
