"""Cross-platform reminder dialog client using Tkinter.

This module offers a minimal, thread-safe API to create, read, update, and delete
simple reminder dialogs on Windows, macOS, and Linux. It runs a hidden Tk root
window in a background thread and marshals all UI work onto that thread.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError as exc:  # pragma: no cover - environment specific
    raise RuntimeError("Tkinter is required for reminder dialogs") from exc

from loguru import logger


@dataclass
class Reminder:
    reminder_id: str
    title: str
    message: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    window: Optional[tk.Toplevel] = None
    label: Optional[tk.Label] = None


class ReminderClient:
    def __init__(self, poll_interval_ms: int = 80) -> None:
        self._poll_interval_ms = max(16, poll_interval_ms)
        self._queue: "queue.Queue[tuple[Callable, tuple, dict, queue.Queue]]" = (
            queue.Queue()
        )
        self._shutdown_event = threading.Event()
        self._ready = threading.Event()
        self._reminders: Dict[str, Reminder] = {}
        self._event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
        self._event_listeners: List[Callable[[str, dict], None]] = []
        self._event_listeners_lock = threading.Lock()
        self._ui_thread = threading.Thread(
            target=self._run_ui, name="ReminderUI", daemon=True
        )
        self._event_thread = threading.Thread(
            target=self._dispatch_events, name="ReminderEvents", daemon=True
        )
        logger.debug(
            "Starting ReminderClient UI thread",
            extra={"component": "reminder_ui", "poll_interval_ms": poll_interval_ms},
        )
        self._ui_thread.start()
        self._event_thread.start()
        if not self._ready.wait(timeout=5):
            logger.error(
                "Reminder UI thread failed to start",
                extra={"component": "reminder_ui", "timeout": 5},
            )
            raise RuntimeError("Tkinter UI thread did not start")
        logger.info(
            "ReminderClient initialized",
            extra={"component": "reminder_ui", "poll_interval_ms": poll_interval_ms},
        )

    def create_reminder(
        self,
        title: str,
        message: str,
        *,
        topmost: bool = True,
        width_px: Optional[int] = None,
        height_px: Optional[int] = None,
        font_size: Optional[int] = None,
    ) -> str:
        reminder_id = str(uuid.uuid4())
        reminder = Reminder(reminder_id=reminder_id, title=title, message=message)
        self._call_ui(
            self._create_reminder_ui,
            reminder,
            topmost,
            width_px,
            height_px,
            font_size,
        )
        logger.info(
            "Created reminder",
            extra={
                "component": "reminder_ui",
                "action": "create",
                "reminder_id": reminder_id[:8],
                "title": title,
                "topmost": bool(topmost),
                "width_px": width_px,
                "height_px": height_px,
                "font_size": font_size,
            },
        )
        return reminder_id

    def update_reminder(
        self,
        reminder_id: str,
        *,
        title: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self._call_ui(self._update_reminder_ui, reminder_id, title, message)
        logger.info(
            "Updated reminder",
            extra={
                "component": "reminder_ui",
                "action": "update",
                "reminder_id": reminder_id[:8],
                "title": title,
                "message_present": message is not None,
            },
        )

    def delete_reminder(self, reminder_id: str) -> None:
        self._call_ui(self._destroy_reminder_ui, reminder_id)
        logger.info(
            "Deleted reminder",
            extra={
                "component": "reminder_ui",
                "action": "delete",
                "reminder_id": reminder_id[:8],
            },
        )

    def list_reminders(self) -> List[dict]:
        return self._call_ui(self._snapshot_reminders)

    def add_event_listener(
        self, callback: Callable[[str, dict], None]
    ) -> Callable[[], None]:
        """Register a callback to receive reminder lifecycle events.

        The callback receives (event_name, payload) where event_name is one of
        "created", "updated", "deleted", or "dismissed". The returned function
        unregisters the listener.
        """

        with self._event_listeners_lock:
            self._event_listeners.append(callback)

        def _unsubscribe() -> None:
            self.remove_event_listener(callback)

        return _unsubscribe

    def remove_event_listener(self, callback: Callable[[str, dict], None]) -> None:
        with self._event_listeners_lock:
            try:
                self._event_listeners.remove(callback)
            except ValueError:
                pass

    def shutdown(self) -> None:
        if self._shutdown_event.is_set():
            logger.debug(
                "Shutdown already in progress",
                extra={"component": "reminder_ui", "action": "shutdown"},
            )
            return
        logger.info(
            "Shutting down ReminderClient",
            extra={"component": "reminder_ui", "action": "shutdown"},
        )
        self._shutdown_event.set()
        # Nudge the UI loop and event dispatcher so they can exit promptly.
        if self._ready.is_set():
            try:
                self._call_ui(lambda: None)
            except Exception:
                pass
        self._event_queue.put(None)
        self._ui_thread.join(timeout=5)
        if self._ui_thread.is_alive():
            logger.warning(
                "Reminder UI thread did not exit cleanly",
                extra={"component": "reminder_ui", "action": "shutdown"},
            )
        self._event_thread.join(timeout=2)
        if self._event_thread.is_alive():
            logger.warning(
                "Reminder event thread did not exit cleanly",
                extra={"component": "reminder_ui", "action": "shutdown"},
            )
        logger.info(
            "ReminderClient shutdown complete",
            extra={"component": "reminder_ui", "action": "shutdown"},
        )

    # Internal UI helpers

    def _run_ui(self) -> None:
        self._tk = tk.Tk()
        self._tk.withdraw()
        self._ready.set()
        logger.debug("Reminder UI thread ready", extra={"component": "reminder_ui"})
        self._tk.after(self._poll_interval_ms, self._process_queue)
        try:
            self._tk.mainloop()
        finally:
            try:
                self._tk.destroy()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.warning(
                    "Failed to destroy Tk root on shutdown",
                    extra={
                        "component": "reminder_ui",
                        "action": "shutdown",
                        "error": str(exc),
                    },
                )
            # Drop the reference here to ensure Tk cleanup happens on the UI thread.
            self._tk = None
            self._ready.clear()

    def _process_queue(self) -> None:
        while True:
            try:
                func, args, kwargs, response_q = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                result = func(*args, **kwargs)
                response_q.put((True, result))
            except Exception as exc:  # pragma: no cover - surfaced to caller
                logger.error(
                    "Reminder UI task failed",
                    extra={
                        "component": "reminder_ui",
                        "error": str(exc),
                        "func": getattr(func, "__name__", str(func)),
                    },
                )
                response_q.put((False, exc))
        if self._shutdown_event.is_set():
            # Quit the Tk mainloop; root destruction happens in _run_ui cleanup.
            self._tk.quit()
        else:
            self._tk.after(self._poll_interval_ms, self._process_queue)

    def _call_ui(self, func: Callable, *args, **kwargs):
        if self._shutdown_event.is_set():
            raise RuntimeError("Reminder client is shut down")
        response_q: "queue.Queue[tuple[bool, object]]" = queue.Queue()
        self._queue.put((func, args, kwargs, response_q))
        success, payload = response_q.get()
        if success:
            return payload
        raise payload

    def _create_reminder_ui(
        self,
        reminder: Reminder,
        topmost: bool,
        width_px: Optional[int],
        height_px: Optional[int],
        font_size: Optional[int],
    ) -> None:
        win = tk.Toplevel(self._tk)
        win.title(reminder.title)
        try:
            win.attributes("-topmost", bool(topmost))
        except Exception:
            pass
        win.protocol(
            "WM_DELETE_WINDOW",
            lambda rid=reminder.reminder_id: self._destroy_reminder_ui(
                rid, source="user"
            ),
        )

        label_font = None
        if font_size is not None:
            try:
                default_font = tkfont.nametofont("TkDefaultFont")
                label_font = tkfont.Font(
                    family=default_font.actual("family"),
                    size=int(font_size),
                    weight=default_font.actual("weight"),
                    slant=default_font.actual("slant"),
                )
            except Exception:
                label_font = ("TkDefaultFont", int(font_size))

        wraplength = max(48, (width_px - 32)) if width_px else 360
        lbl = tk.Label(
            win,
            text=reminder.message,
            padx=12,
            pady=12,
            justify="left",
            wraplength=wraplength,
            font=label_font,
        )
        lbl.pack(fill="both", expand=True)
        btn = tk.Button(
            win,
            text="Dismiss",
            command=lambda rid=reminder.reminder_id: self._destroy_reminder_ui(
                rid, source="user"
            ),
        )
        btn.pack(pady=(0, 10))

        reminder.window = win
        reminder.label = lbl
        reminder.updated_at = time.time()
        self._reminders[reminder.reminder_id] = reminder
        self._emit_event(
            "created",
            {
                "id": reminder.reminder_id,
                "title": reminder.title,
                "message": reminder.message,
                "created_at": reminder.created_at,
                "updated_at": reminder.updated_at,
            },
        )

        # Apply the requested geometry after the widgets exist so requested sizes are accurate.
        if width_px is not None or height_px is not None:
            win.update_idletasks()
            target_w = width_px if width_px is not None else win.winfo_width()
            target_h = height_px if height_px is not None else win.winfo_height()
            win.geometry(f"{max(1, int(target_w))}x{max(1, int(target_h))}")

    def _update_reminder_ui(
        self, reminder_id: str, title: Optional[str], message: Optional[str]
    ) -> None:
        reminder = self._reminders.get(reminder_id)
        if not reminder:
            logger.error(
                "Attempted to update unknown reminder",
                extra={
                    "component": "reminder_ui",
                    "action": "update",
                    "reminder_id": reminder_id[:8],
                },
            )
            raise KeyError(f"Unknown reminder: {reminder_id}")
        if title is not None:
            reminder.title = title
            if reminder.window:
                reminder.window.title(title)
        if message is not None:
            reminder.message = message
            if reminder.label:
                reminder.label.configure(text=message)
        reminder.updated_at = time.time()
        self._emit_event(
            "updated",
            {
                "id": reminder.reminder_id,
                "title": reminder.title,
                "message": reminder.message,
                "created_at": reminder.created_at,
                "updated_at": reminder.updated_at,
            },
        )

    def _destroy_reminder_ui(self, reminder_id: str, *, source: str = "api") -> None:
        reminder = self._reminders.pop(reminder_id, None)
        if not reminder:
            return
        if reminder.window:
            reminder.window.destroy()
        reminder.window = None
        reminder.label = None
        event_name = "dismissed" if source == "user" else "deleted"
        self._emit_event(
            event_name,
            {
                "id": reminder_id,
                "title": reminder.title if reminder else None,
                "message": reminder.message if reminder else None,
                "created_at": reminder.created_at if reminder else None,
                "updated_at": reminder.updated_at if reminder else None,
                "source": source,
            },
        )

    def _snapshot_reminders(self) -> List[dict]:
        now = time.time()
        return [
            {
                "id": r.reminder_id,
                "title": r.title,
                "message": r.message,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "age_seconds": max(0.0, now - r.created_at),
            }
            for r in self._reminders.values()
        ]

    def _emit_event(self, event_name: str, payload: dict) -> None:
        # Non-blocking enqueue so UI thread is never delayed by listeners.
        self._event_queue.put((event_name, payload))

    def _dispatch_events(self) -> None:
        while True:
            try:
                item = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                if self._shutdown_event.is_set():
                    break
                continue

            if item is None:
                break

            event_name, payload = item
            with self._event_listeners_lock:
                listeners_snapshot = list(self._event_listeners)

            for listener in listeners_snapshot:
                try:
                    listener(event_name, payload)
                except Exception as exc:  # pragma: no cover - listener-controlled
                    logger.error(
                        "Reminder event listener failed",
                        extra={
                            "component": "reminder_ui",
                            "event": event_name,
                            "error": str(exc),
                        },
                    )


# Example usage
if __name__ == "__main__":
    import sys
    import time

    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <level>{message}</level> | {extra}",
        level="DEBUG",
    )

    client = ReminderClient()

    def on_event(event: str, payload: dict) -> None:
        print(f"EVENT {event}: {payload}")

    unsubscribe = client.add_event_listener(on_event)
    reminder_id = client.create_reminder(
        "Stretch",
        "Stand up and stretch your legs.",
        topmost=True,
        width_px=420,
        height_px=220,
        font_size=14,
    )
    print("Created reminder", reminder_id)
    time.sleep(2)

    client.update_reminder(reminder_id, message="Time to stretch now.")
    print("Updated reminder message")
    time.sleep(5)
    print("Current reminders:", client.list_reminders())
    time.sleep(3)

    client.delete_reminder(reminder_id)
    print("Deleted reminder")
    time.sleep(1)

    unsubscribe()

    client.shutdown()
    print("Shutdown complete")
