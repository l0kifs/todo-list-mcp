"""Background reminder sidecar using ReminderClient and SoundClient."""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

from loguru import logger

from todo_list_mcp.reminder_client import ReminderClient
from todo_list_mcp.sound_client import SoundClient


@dataclass(order=True)
class _ScheduledReminder:
    run_at: float
    task_filename: str
    title: str
    message: str
    reminder_iso: str


class ReminderSidecar:
    def __init__(self) -> None:
        self._queue: list[_ScheduledReminder] = []
        self._seen_keys: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._reminder_client: Optional[ReminderClient] = None
        self._sound_client: Optional[SoundClient] = None
        self._thread = threading.Thread(target=self._runner, daemon=True)
        self._thread.start()

    def initialize_clients(self) -> None:
        try:
            self._reminder_client = ReminderClient()
            self._sound_client = SoundClient()
            logger.info("Reminder sidecar clients initialized")
        except Exception as exc:
            logger.warning("Failed to init reminder/sound clients", error=str(exc))

    def shutdown(self) -> None:
        self._shutdown.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._reminder_client:
            try:
                self._reminder_client.shutdown()
            except Exception:
                pass
        if self._sound_client:
            try:
                self._sound_client.shutdown()
            except Exception:
                pass

    def schedule_from_task(self, task_filename: str, task: Any) -> None:
        reminders = _as_str_list(_get_value(task, "reminders"))
        if not reminders:
            return
        title = _get_value(task, "title") or task_filename
        message = _get_value(task, "description") or "Reminder"
        for reminder_iso in reminders:
            dt = _parse_iso(reminder_iso)
            if dt is None:
                logger.warning(
                    "Skipping invalid reminder timestamp",
                    extra={"task": task_filename, "value": reminder_iso},
                )
                continue
            if dt < datetime.now(tz=UTC):
                continue
            key = (task_filename, reminder_iso)
            with self._lock:
                if key in self._seen_keys:
                    continue
                self._seen_keys.add(key)
                heapq.heappush(
                    self._queue,
                    _ScheduledReminder(
                        run_at=dt.timestamp(),
                        task_filename=task_filename,
                        title=title,
                        message=message,
                        reminder_iso=reminder_iso,
                    ),
                )

    def _runner(self) -> None:
        self.initialize_clients()
        while not self._shutdown.is_set():
            now = time.time()
            item: Optional[_ScheduledReminder] = None
            with self._lock:
                if self._queue and self._queue[0].run_at <= now:
                    item = heapq.heappop(self._queue)
            if item:
                self._deliver(item)
                continue
            time.sleep(0.25)

    def _deliver(self, item: _ScheduledReminder) -> None:
        logger.info(
            "Delivering reminder",
            extra={"task": item.task_filename, "reminder": item.reminder_iso},
        )
        try:
            if self._reminder_client:
                self._reminder_client.create_reminder(item.title, item.message)
        except Exception as exc:
            logger.warning("Reminder popup failed", error=str(exc))
        try:
            if self._sound_client:
                self._sound_client.create_sound()
        except Exception as exc:
            logger.warning("Reminder sound failed", error=str(exc))


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return []


def _get_value(obj: Any, key: str) -> Optional[Any]:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None
