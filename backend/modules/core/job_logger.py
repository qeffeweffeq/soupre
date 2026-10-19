import asyncio
import contextvars
import logging
import os
import shutil
from collections import defaultdict, deque
from typing import Optional, Set, Dict, Deque

# Context variable tracking current active job_id per asyncio task / thread
current_job_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "current_job_id", default=None
)

DONE_SENTINEL = "__DONE__"


class JobLogManager:
    """
    Manages per-job log buffers, log files, and SSE subscribers.
    Thread-safe and coroutine-safe.
    """

    def __init__(self, max_history: int = 2000):
        self.max_history = max_history
        self._history: Dict[int, Deque[str]] = defaultdict(
            lambda: deque(maxlen=self.max_history)
        )
        self._subscribers: Dict[int, Set[asyncio.Queue]] = defaultdict(set)
        self._file_paths: Dict[int, str] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def register_job_log_file(self, job_id: int, log_file_path: str):
        self._file_paths[job_id] = log_file_path
        os.makedirs(os.path.dirname(os.path.abspath(log_file_path)), exist_ok=True)

    def log(self, job_id: int, message: str):
        # 1. Store in memory buffer
        self._history[job_id].append(message)

        # 2. Append to disk file if registered
        log_file = self._file_paths.get(job_id)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(message + "\n")
            except Exception:
                pass

        # 3. Broadcast to all active SSE queues for this job
        if self._loop and self._loop.is_running():
            for q in list(self._subscribers.get(job_id, [])):
                try:
                    asyncio.run_coroutine_threadsafe(q.put(message), self._loop)
                except Exception:
                    pass

    def subscribe(self, job_id: int, fallback_path: Optional[str] = None) -> tuple[list[str], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[job_id].add(q)
        history = self.get_history(job_id, fallback_path=fallback_path)
        return history, q

    def unsubscribe(self, job_id: int, q: asyncio.Queue):
        if job_id in self._subscribers and q in self._subscribers[job_id]:
            self._subscribers[job_id].remove(q)
            if not self._subscribers[job_id]:
                del self._subscribers[job_id]

    def get_history(self, job_id: int, fallback_path: Optional[str] = None) -> list[str]:
        if job_id in self._history and len(self._history[job_id]) > 0:
            return list(self._history[job_id])

        # Fallback to reading disk file if in-memory buffer was cleared or on server restart
        log_file = self._file_paths.get(job_id) or fallback_path
        if log_file and os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = [line.rstrip("\r\n") for line in f.readlines()]
                    return lines[-self.max_history:]
            except Exception:
                pass
        return []

    async def close_job(self, job_id: int):
        # Broadcast DONE sentinel to all active subscribers
        for q in list(self._subscribers.get(job_id, [])):
            await q.put(DONE_SENTINEL)
        self._subscribers.pop(job_id, None)

    def preserve_log_file(self, job_id: int, archive_dir: str, fallback_path: Optional[str] = None) -> Optional[str]:
        """Move / copy job.log to an archive location when deleting job assets."""
        log_file = self._file_paths.get(job_id) or fallback_path
        if log_file and os.path.exists(log_file):
            os.makedirs(archive_dir, exist_ok=True)
            parent_dir = os.path.basename(os.path.dirname(os.path.abspath(log_file)))
            dest = os.path.join(archive_dir, f"{parent_dir}.log")
            try:
                shutil.copy2(log_file, dest)
                return dest
            except Exception:
                pass
        return None

    def cleanup_job(self, job_id: int):
        self._history.pop(job_id, None)
        self._subscribers.pop(job_id, None)
        self._file_paths.pop(job_id, None)


job_log_manager = JobLogManager()


class JobContextFilter(logging.Filter):
    """Injects current_job_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = current_job_id.get(None)
        return True


class JobLogHandler(logging.Handler):
    """Routes logs with job_id to the JobLogManager."""

    def emit(self, record: logging.LogRecord):
        job_id = getattr(record, "job_id", None)
        if job_id is not None:
            msg = self.format(record)
            job_log_manager.log(job_id, msg)
