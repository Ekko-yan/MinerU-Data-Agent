from __future__ import annotations

import os
import subprocess
from threading import Lock

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is expected with MinerU deps.
    psutil = None  # type: ignore[assignment]


_lock = Lock()
_processes: dict[str, subprocess.Popen[str]] = {}


def register_process(job_id: str, process: subprocess.Popen[str]) -> None:
    with _lock:
        _processes[job_id] = process


def unregister_process(job_id: str) -> None:
    with _lock:
        _processes.pop(job_id, None)


def _kill_registered_process(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    return True


def _kill_psutil_tree(pid: int) -> bool:
    if psutil is None:
        return False
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return False

    children = parent.children(recursive=True)
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            continue
    try:
        parent.kill()
    except psutil.Error:
        pass
    _, alive = psutil.wait_procs([parent, *children], timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            continue
    return True


def _find_job_process_ids(job_id: str) -> list[int]:
    if psutil is None:
        return []

    current_pid = os.getpid()
    matches: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(process.info["pid"])
            cmdline = " ".join(process.info.get("cmdline") or [])
        except (psutil.Error, TypeError, ValueError):
            continue
        if pid == current_pid:
            continue
        if job_id in cmdline:
            matches.append(pid)
    return matches


def find_process_ids(job_id: str) -> list[int]:
    return _find_job_process_ids(job_id)


def has_process(job_id: str) -> bool:
    return bool(_find_job_process_ids(job_id))


def cancel_process(job_id: str) -> bool:
    with _lock:
        process = _processes.pop(job_id, None)

    cancelled = False
    if process is not None:
        cancelled = _kill_registered_process(process)

    for pid in _find_job_process_ids(job_id):
        cancelled = _kill_psutil_tree(pid) or cancelled
    return cancelled
