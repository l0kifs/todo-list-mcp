"""Cross-platform reminder dialog client using wxPython.

This module offers a minimal, thread-safe API to create, read, update, and delete
simple reminder dialogs on Windows, macOS, and Linux. It runs a hidden wx.App
in a background thread and marshals all UI work onto that thread.
"""

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import platform

try:
    import wx
except ImportError as exc:  # pragma: no cover - environment specific
    raise RuntimeError("wxPython is required for reminder dialogs") from exc

from loguru import logger


@dataclass
class Reminder:
    reminder_id: str
    title: str
    message: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    frame: Optional[wx.Frame] = None
    text: Optional[wx.StaticText] = None


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
        self._platform = platform.system()
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
        self._event_thread.start()
        if self._platform != "Darwin":
            self._ui_thread.start()
            if not self._ready.wait(timeout=5):
                logger.error(
                    "Reminder UI thread failed to start",
                    extra={"component": "reminder_ui", "timeout": 5},
                )
                raise RuntimeError("wxPython UI thread did not start")
        logger.info(
            "ReminderClient initialized",
            extra={"component": "reminder_ui", "poll_interval_ms": poll_interval_ms},
        )

    def start_ui(self) -> None:
        if self._platform == "Darwin":
            self._run_ui()

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
        if self._platform != "Darwin" and self._ui_thread.is_alive():
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
        self._app = wx.App(False)
        # Create a hidden main frame
        self._hidden_frame = wx.Frame(None, wx.ID_ANY, "")
        self._hidden_frame.Hide()
        self._ready.set()
        logger.debug("Reminder UI thread ready", extra={"component": "reminder_ui"})
        # Start the timer for processing the queue
        self._timer = wx.Timer(self._hidden_frame)
        self._hidden_frame.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._timer.Start(self._poll_interval_ms)
        try:
            self._app.MainLoop()
        finally:
            try:
                self._timer.Stop()
                self._hidden_frame.Destroy()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.warning(
                    "Failed to destroy wx Frame on shutdown",
                    extra={
                        "component": "reminder_ui",
                        "action": "shutdown",
                        "error": str(exc),
                    },
                )
            # Drop the reference here to ensure wx cleanup happens on the UI thread.
            self._app = None
            self._ready.clear()

    def _on_timer(self, event) -> None:
        self._process_queue()

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
            # Exit the wx MainLoop
            self._app.ExitMainLoop()

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
        # Create frame style with STAY_ON_TOP if topmost
        style = wx.DEFAULT_FRAME_STYLE
        if topmost:
            style |= wx.STAY_ON_TOP

        frame = wx.Frame(None, wx.ID_ANY, reminder.title, style=style)

        # Create a panel for better cross-platform look
        panel = wx.Panel(frame)
        
        # Create sizer
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Create static text for message
        wraplength = max(48, (width_px - 32)) if width_px else 360
        text = wx.StaticText(panel, wx.ID_ANY, reminder.message)
        
        # Set font if specified
        if font_size is not None:
            font = text.GetFont()
            font.SetPointSize(int(font_size))
            text.SetFont(font)
        
        # Wrap text to fit width
        text.Wrap(wraplength)
        
        sizer.Add(text, 1, wx.ALL | wx.EXPAND, 12)
        
        # Create dismiss button
        btn = wx.Button(panel, wx.ID_ANY, "Dismiss")
        btn.Bind(wx.EVT_BUTTON, lambda evt, rid=reminder.reminder_id: self._on_dismiss(rid))
        sizer.Add(btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        
        panel.SetSizer(sizer)
        
        # Bind close event
        frame.Bind(wx.EVT_CLOSE, lambda evt, rid=reminder.reminder_id: self._on_close(evt, rid))
        
        # Finalize layout before calculating size
        panel.Layout()
        sizer.Fit(panel)
        
        # Set size if specified, otherwise use calculated best size
        if width_px is not None or height_px is not None:
            best_size = panel.GetBestSize()
            target_w = width_px if width_px is not None else best_size.GetWidth()
            target_h = height_px if height_px is not None else best_size.GetHeight()
            # Add some padding for frame decorations
            frame.SetClientSize(max(200, int(target_w)), max(100, int(target_h)))
        else:
            # Use panel's best size with minimum dimensions
            best_size = panel.GetBestSize()
            frame.SetClientSize(max(300, best_size.GetWidth()), max(120, best_size.GetHeight()))
        
        # Center the frame on screen
        frame.Centre()
        
        # Show the frame
        frame.Show(True)
        
        reminder.frame = frame
        reminder.text = text
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

    def _on_dismiss(self, reminder_id: str) -> None:
        """Handle dismiss button click."""
        wx.CallAfter(lambda: self._destroy_reminder_ui(reminder_id, source="user"))

    def _on_close(self, event, reminder_id: str) -> None:
        """Handle window close event."""
        event.Skip()  # Allow the window to close
        wx.CallAfter(lambda: self._destroy_reminder_ui(reminder_id, source="user"))

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
            if reminder.frame:
                reminder.frame.SetTitle(title)
        if message is not None:
            reminder.message = message
            if reminder.text:
                reminder.text.SetLabel(message)
                reminder.text.Wrap(reminder.text.GetSize().GetWidth())
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
        if reminder.frame:
            # Unbind events to prevent recursive calls
            reminder.frame.Unbind(wx.EVT_CLOSE)
            if not reminder.frame.IsBeingDeleted():
                reminder.frame.Destroy()
        reminder.frame = None
        reminder.text = None
        event_name = "dismissed" if source == "user" else "deleted"
        self._emit_event(
            event_name,
            {
                "id": reminder_id,
                "title": reminder.title,
                "message": reminder.message,
                "created_at": reminder.created_at,
                "updated_at": reminder.updated_at,
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

    def demo(client):
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

    client = ReminderClient()

    if platform.system() == "Darwin":
        demo_thread = threading.Thread(target=demo, args=(client,))
        demo_thread.start()
        client.start_ui()
    else:
        demo(client)
