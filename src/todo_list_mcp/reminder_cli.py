#!/usr/bin/env python3
"""Standalone reminder daemon with CLI interface using typer.

A simple, persistent reminder service that stores reminders in JSON format
and delivers desktop notifications when they're due.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from todo_list_mcp.logging_config import setup_logging
from todo_list_mcp.wxpython_reminder_client import ReminderClient
from todo_list_mcp.settings import get_settings
from todo_list_mcp.sound_client import SoundClient

# Get settings
settings = get_settings()

# Setup logging
setup_logging(settings)

# Constants derived from settings
APP_DATA_DIR = Path(settings.app_data_dir)
REMINDERS_FILE = APP_DATA_DIR / "reminder_daemon" / "reminders.json"
PID_FILE = APP_DATA_DIR / "reminder_daemon" / "daemon.pid"
console = Console()
app = typer.Typer(help="Reminder daemon - persistent reminder service")


@dataclass
class Reminder:
    """A single reminder entry."""

    id: str
    title: str
    message: str
    due_at: str  # ISO 8601 timestamp
    created_at: str  # ISO 8601 timestamp
    task_filename: Optional[str] = (
        None  # Related task file (e.g., 'tasks/my-task.yaml')
    )

    def is_due(self) -> bool:
        """Check if the reminder is due."""
        try:
            due_dt = _parse_iso(self.due_at)
            return due_dt is not None and due_dt <= datetime.now(tz=UTC)
        except Exception:
            return False


class ReminderStore:
    """Manages reminder persistence to JSON file."""

    def __init__(self, file_path: Path = REMINDERS_FILE) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> List[Reminder]:
        """Load all reminders from file."""
        with self._lock:
            if not self.file_path.exists():
                return []
            try:
                with open(self.file_path, "r") as f:
                    data = json.load(f)
                return [Reminder(**item) for item in data]
            except Exception as e:
                logger.error(f"Failed to load reminders: {e}")
                return []

    def save(self, reminders: List[Reminder]) -> None:
        """Save all reminders to file."""
        with self._lock:
            try:
                with open(self.file_path, "w") as f:
                    json.dump([asdict(r) for r in reminders], f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save reminders: {e}")

    def add(self, reminder: Reminder) -> None:
        """Add a new reminder."""
        reminders = self.load()
        reminders.append(reminder)
        self.save(reminders)

    def remove(self, reminder_ids: List[str]) -> int:
        """Remove reminders by IDs. Returns count of removed reminders."""
        reminders = self.load()
        original_count = len(reminders)
        reminders = [r for r in reminders if r.id not in reminder_ids]
        self.save(reminders)
        return original_count - len(reminders)

    def clear(self) -> int:
        """Remove all reminders. Returns count of removed reminders."""
        reminders = self.load()
        count = len(reminders)
        self.save([])
        return count


class ReminderDaemon:
    """Background daemon that checks and delivers reminders."""

    def __init__(self, store: ReminderStore) -> None:
        self.store = store
        self._shutdown = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._reminder_client: Optional[ReminderClient] = None
        self._sound_client: Optional[SoundClient] = None

    def start(self) -> None:
        """Start the daemon thread."""
        try:
            self._reminder_client = ReminderClient()
            self._sound_client = SoundClient()
            logger.info("Reminder daemon initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize clients: {e}")

        self._thread.start()

    def stop(self) -> None:
        """Stop the daemon thread."""
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

    def _run(self) -> None:
        """Main daemon loop."""
        while not self._shutdown.is_set():
            try:
                reminders = self.store.load()
                due_reminders = [r for r in reminders if r.is_due()]

                for reminder in due_reminders:
                    self._deliver(reminder)

                if due_reminders:
                    # Remove delivered reminders
                    remaining = [r for r in reminders if not r.is_due()]
                    self.store.save(remaining)

            except Exception as e:
                logger.error(f"Daemon error: {e}")

            time.sleep(1)  # Check every second

    def _deliver(self, reminder: Reminder) -> None:
        """Deliver a reminder notification."""
        logger.info(f"Delivering reminder: {reminder.title}")
        try:
            if self._reminder_client:
                self._reminder_client.create_reminder(reminder.title, reminder.message)
        except Exception as e:
            logger.warning(f"Reminder popup failed: {e}")

        try:
            if self._sound_client:
                self._sound_client.create_sound()
        except Exception as e:
            logger.warning(f"Reminder sound failed: {e}")


# Helper functions
def _parse_iso(value: str) -> Optional[datetime]:
    """Parse ISO 8601 timestamp."""
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _now_iso() -> str:
    """Get current time as ISO 8601 string."""
    return datetime.now(tz=UTC).isoformat()


def _generate_id() -> str:
    """Generate a unique reminder ID."""
    import uuid

    return uuid.uuid4().hex[:8]


def _is_daemon_running() -> bool:
    """Check if daemon is already running by checking PID file and process."""
    import os

    if not PID_FILE.exists():
        return False

    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())

        # Check if process with this PID exists
        try:
            os.kill(pid, 0)  # Signal 0 just checks if process exists
            return True
        except OSError:
            # Process doesn't exist, remove stale PID file
            PID_FILE.unlink()
            return False
    except (ValueError, IOError):
        return False


def _write_pid_file() -> None:
    """Write current process PID to file."""
    import os

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid_file() -> None:
    """Remove PID file on shutdown."""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


# CLI Commands
@app.command()
def add(
    title: str = typer.Argument(..., help="Reminder title"),
    message: str = typer.Argument(..., help="Reminder message"),
    due_at: str = typer.Argument(..., help="Due time (ISO 8601 format)"),
    task_filename: Optional[str] = typer.Option(
        None, "--task", "-t", help="Related task filename"
    ),
) -> None:
    """Add a new reminder."""
    # Validate due_at format
    due_dt = _parse_iso(due_at)
    if due_dt is None:
        console.print("[red]Error: Invalid timestamp format. Use ISO 8601.[/red]")
        raise typer.Exit(code=1)

    if due_dt < datetime.now(tz=UTC):
        console.print("[yellow]Warning: Reminder is in the past.[/yellow]")

    store = ReminderStore()
    reminder = Reminder(
        id=_generate_id(),
        title=title,
        message=message,
        due_at=due_at,
        created_at=_now_iso(),
        task_filename=task_filename,
    )
    store.add(reminder)
    console.print(f"[green]✓[/green] Reminder added: {reminder.id}")


@app.command()
def list() -> None:
    """List all reminders."""
    store = ReminderStore()
    reminders = store.load()

    if not reminders:
        console.print("[dim]No reminders found.[/dim]")
        return

    table = Table(title="Reminders", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Message")
    table.add_column("Due At", style="yellow")
    table.add_column("Task", style="magenta")
    table.add_column("Status")

    for reminder in reminders:
        status = "[red]DUE[/red]" if reminder.is_due() else "[green]PENDING[/green]"
        task_display = reminder.task_filename or "[dim]-[/dim]"
        table.add_row(
            reminder.id,
            reminder.title,
            reminder.message,
            reminder.due_at,
            task_display,
            status,
        )

    console.print(table)


@app.command()
def remove(
    ids: List[str] = typer.Argument(default=None, help="Reminder IDs to remove"),
    all: bool = typer.Option(False, "--all", "-a", help="Remove all reminders"),
) -> None:
    """Remove one or more reminders."""
    store = ReminderStore()

    if all:
        if not typer.confirm("Remove all reminders?"):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit()
        count = store.clear()
        console.print(f"[green]✓[/green] Removed {count} reminder(s)")
    elif ids:
        count = store.remove(ids)
        console.print(f"[green]✓[/green] Removed {count} reminder(s)")
    else:
        console.print("[red]Error: Specify reminder IDs or use --all flag[/red]")
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Check if the daemon is running."""
    if _is_daemon_running():
        try:
            with open(PID_FILE, "r") as f:
                pid = f.read().strip()
            console.print(f"[green]✓[/green] Daemon is running (PID: {pid})")
        except Exception:
            console.print("[green]✓[/green] Daemon is running")
    else:
        console.print("[red]✗[/red] Daemon is not running")
        raise typer.Exit(code=1)


@app.command()
def daemon() -> None:
    """Run the reminder daemon (persistent mode)."""
    import platform
    import os
    
    # Check if daemon is already running
    if _is_daemon_running():
        console.print("[yellow]Daemon is already running![/yellow]")
        console.print(f"[dim]PID file: {PID_FILE}[/dim]")
        raise typer.Exit(code=1)

    console.print("[bold green]Starting reminder daemon...[/bold green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    # Write PID file
    _write_pid_file()

    store = ReminderStore()
    daemon_instance = ReminderDaemon(store)
    
    # Handle clean shutdown
    def signal_handler(sig, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        daemon_instance.stop()
        _remove_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # On macOS, wxPython must run on the main thread
    if platform.system() == "Darwin":
        logger.info("macOS detected - running wxPython on main thread")
        daemon_instance.start()
        # Run the wxPython event loop on the main thread
        if daemon_instance._reminder_client:
            try:
                daemon_instance._reminder_client.start_ui()
            except KeyboardInterrupt:
                signal_handler(None, None)
    else:
        # On other platforms, we can run everything in background threads
        daemon_instance.start()
        # Keep running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            signal_handler(None, None)


if __name__ == "__main__":
    app()
