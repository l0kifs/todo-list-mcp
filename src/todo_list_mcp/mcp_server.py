"""Todo List MCP server single-file entrypoint.

Provides create/read/update/archive/list operations over YAML tasks stored in
GitHub under the tasks/ directory. Archives move files into archive/.

Reminder management is handled via the standalone reminder_cli daemon,
which is automatically started when the MCP server starts (if not already running).
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

import yaml
from fastmcp import FastMCP
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, model_validator

from todo_list_mcp.github_file_client import GitHubFileClient
from todo_list_mcp.logging_config import setup_logging
from todo_list_mcp.settings import get_settings

# ---------------------------------------------------------------------------
# Models

Status = Literal["open", "in-progress", "done"]
Priority = Literal["low", "medium", "high"]
Urgency = Literal["low", "medium", "high"]


class TaskPayload(BaseModel):
    title: str = Field(description="Task title or summary")
    description: Optional[str] = Field(
        None, description="Detailed task description or notes"
    )
    status: Status = Field(
        "open", description="Task status: 'open', 'in-progress', or 'done'"
    )
    priority: Priority = Field(
        "medium", description="Task priority: 'low', 'medium', or 'high'"
    )
    urgency: Urgency = Field(
        "medium", description="Task urgency level: 'low', 'medium', or 'high'"
    )
    time_estimate: Optional[float] = Field(
        None,
        description="Estimated time to complete in hours (e.g., 1.5 for 1.5 hours)",
    )
    due_date: Optional[str] = Field(
        None, description="Due date in ISO 8601 format (e.g., '2026-01-15T10:00:00Z')"
    )
    tags: List[str] = Field(
        default_factory=list, description="List of tags for categorization"
    )
    assignee: Optional[str] = Field(
        None, description="Person or entity assigned to the task"
    )
    created_at: Optional[str] = Field(
        None, description="Task creation timestamp in ISO 8601 format"
    )
    updated_at: Optional[str] = Field(
        None, description="Last update timestamp in ISO 8601 format"
    )

    @model_validator(mode="before")
    def normalize_lists(cls, values: dict) -> dict:  # type: ignore[override]
        tags = values.get("tags")
        if tags is None:
            values["tags"] = []
        return values




# ---------------------------------------------------------------------------
# Helpers


def _slugify(text: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe or "task"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _priority_order(priority: str) -> int:
    order = {"high": 0, "medium": 1, "low": 2}
    return order.get(priority, 3)


def _task_path(filename: str) -> str:
    if filename.startswith("tasks/"):
        return filename
    if filename.startswith("archive/"):
        return filename
    return f"tasks/{filename}"


# ---------------------------------------------------------------------------
# MCP server setup

settings = get_settings()
setup_logging(settings)

client = GitHubFileClient(settings=settings.github_file_client_settings)

app = FastMCP(name=settings.app_name, version=settings.app_version)


# ---------------------------------------------------------------------------
# Tools


@app.tool()
def create_tasks(
    tasks: Annotated[
        List[dict],
        """List of tasks to create. Each task is a dictionary with the following schema:
        
        Required fields:
        - title (str): Task title or summary
        
        Optional fields:
        - description (str | None): Detailed task description or notes
        - status (str): Task status, one of: "open" (default), "in-progress", "done"
        - priority (str): Task priority, one of: "low", "medium" (default), "high"
        - urgency (str): Task urgency level, one of: "low", "medium" (default), "high"
        - time_estimate (float | None): Estimated time to complete in hours (e.g., 1.5 for 1.5 hours)
        - due_date (str | None): Due date in ISO 8601 format (e.g., "2026-01-15T10:00:00Z")
        - tags (list[str]): List of tags for categorization (default: [])
        - assignee (str | None): Person or entity assigned to the task
        - created_at (str | None): Task creation timestamp in ISO 8601 format (auto-set if not provided)
        - updated_at (str | None): Last update timestamp in ISO 8601 format (auto-set if not provided)
        
        Example task dict:
        {
            "title": "Review PR",
            "description": "Review the pull request for the new feature",
            "status": "open",
            "priority": "high",
            "urgency": "high",
            "time_estimate": 2.5,
            "due_date": "2026-01-15T10:00:00Z",
            "tags": ["code-review", "urgent"],
            "assignee": "John Doe"
        }""",
    ],
    filenames: Annotated[
        Optional[List[str]],
        "Optional explicit filenames for tasks (e.g., ['my-task.yaml']). If not provided, filenames are auto-generated from titles",
    ] = None,
) -> dict:
    """Create one or more tasks in the GitHub repository.

    Tasks are stored as YAML files in the tasks/ directory. Each task is automatically
    assigned timestamps.

    Example: Create a single task with default filename:
    tasks=[{"title": "Review PR", "priority": "high", "urgency": "high", "time_estimate": 2.5, "due_date": "2026-01-15T10:00:00Z"}]

    Example: Create multiple tasks with custom filenames:
    tasks=[{"title": "Task 1"}, {"title": "Task 2"}], filenames=["task1.yaml", "task2.yaml"]
    """
    filenames = filenames or []
    task_objects = [TaskPayload(**task) for task in tasks]
    outputs = []
    file_pairs: List[tuple[str, str]] = []
    for idx, task in enumerate(task_objects):
        now_iso = _now_iso()
        task.created_at = task.created_at or now_iso
        task.updated_at = now_iso
        filename = (
            filenames[idx]
            if idx < len(filenames) and filenames[idx]
            else f"{_slugify(task.title)}-{uuid.uuid4().hex[:8]}.yaml"
        )
        full_path = _task_path(filename)
        content = _serialize_task(task)
        file_pairs.append((full_path, content))
        outputs.append({"filename": full_path, "task": task.dict()})
    created = client.create_files(file_pairs)
    return {"created": [c.path for c in created]}


@app.tool()
def read_tasks(
    filenames: Annotated[
        List[str],
        "List of task filenames to read (e.g., ['task-abc123.yaml', 'my-task.yaml']). Returns complete task data including all fields and metadata.",
    ],
) -> dict:
    """Read one or more tasks from the GitHub repository by filename.

    Returns complete task data including all fields and metadata. Use this to retrieve
    task details for review or before updating.

    Example: filenames=["review-pr-abc123.yaml", "fix-bug-def456.yaml"]
    """
    results = []
    for name in filenames:
        path = _task_path(name)
        file = client.read_file(path)
        task = yaml.safe_load(file.content) or {}
        results.append({"filename": path, "task": task, "sha": file.sha})
    return {"tasks": results}


@app.tool()
def update_tasks(
    updates: Annotated[
        List[dict],
        """List of updates, each containing 'filename' and fields to update. Only provided fields are updated; others remain unchanged.
        
        Required fields:
        - filename (str): Task filename to update (e.g., "task-abc123.yaml" or "tasks/task-abc123.yaml")
        
        Optional fields (any task fields can be updated):
        - title (str): Task title or summary
        - description (str | None): Detailed task description or notes
        - status (str): Task status, one of: "open", "in-progress", "done"
        - priority (str): Task priority, one of: "low", "medium", "high"
        - urgency (str): Task urgency level, one of: "low", "medium", "high"
        - time_estimate (float | None): Estimated time to complete in hours (e.g., 1.5 for 1.5 hours)
        - due_date (str | None): Due date in ISO 8601 format (e.g., "2026-01-15T10:00:00Z")
        - tags (list[str]): List of tags for categorization
        - assignee (str | None): Person or entity assigned to the task
        
        Note: created_at and updated_at are automatically managed (updated_at is auto-set on each update).
        
        Example update dict:
        {
            "filename": "review-pr-abc123.yaml",
            "status": "done",
            "priority": "high",
            "urgency": "low",
            "assignee": "John Doe"
        }""",
    ],
) -> dict:
    """Update one or more existing tasks in the GitHub repository.

    Each update must specify a filename and the fields to modify. Only provided fields
    are updated; others remain unchanged. The updated_at timestamp is automatically set.

    Example: Mark a task as done and change priority:
    updates=[{"filename": "review-pr.yaml", "status": "done", "priority": "high", "urgency": "low"}]

    Example: Update multiple tasks:
    updates=[{"filename": "task1.yaml", "status": "in-progress", "time_estimate": 3.0}, {"filename": "task2.yaml", "assignee": "John"}]
    """
    updated_paths: List[str] = []
    for item in updates:
        filename_str = item.get("filename")
        if not filename_str:
            continue
        filename = _task_path(filename_str)
        existing = client.read_file(filename)
        data = yaml.safe_load(existing.content) or {}
        merged = {**data, **{k: v for k, v in item.items() if k != "filename"}}
        merged.setdefault("created_at", data.get("created_at") or _now_iso())
        merged["updated_at"] = _now_iso()
        task_obj = TaskPayload(**merged)
        content = _serialize_task(task_obj)
        client.update_file(filename, content, sha=existing.sha)
        updated_paths.append(filename)
    return {"updated": updated_paths}


@app.tool()
def archive_tasks(
    filenames: Annotated[
        List[str],
        "List of task filenames to archive (e.g., ['completed-task.yaml']). Tasks are moved from tasks/ to archive/ directory, removing them from active task lists while preserving their data.",
    ],
) -> dict:
    """Archive completed or obsolete tasks by moving them to the archive/ directory.

    Archived tasks are moved from tasks/ to archive/ in the GitHub repository, removing
    them from active task lists while preserving their data for future reference.

    Example: filenames=["completed-task.yaml", "old-task.yaml"]
    """
    archived: List[str] = []
    for name in filenames:
        src = _task_path(name)
        tgt_name = Path(name).name
        tgt = f"archive/{tgt_name}"
        client.move_file(src, tgt)
        archived.append(tgt)
    return {"archived": archived}


@app.tool()
def list_tasks(
    status: Annotated[
        Optional[list[Literal["open", "in-progress", "done"]]],
        """Filter by status(es): list of 'open', 'in-progress', or 'done'. 
        Can specify one or more statuses (e.g., ["open"], ["open", "in-progress"]).
        If not provided, all statuses are included."""
    ] = None,
    priority: Annotated[
        Optional[list[Literal["low", "medium", "high"]]],
        """Filter by priority(ies): list of 'low', 'medium', or 'high'. 
        Can specify one or more priorities (e.g., ["high"], ["high", "medium"]).
        If not provided, all priorities are included."""
    ] = None,
    urgency: Annotated[
        Optional[list[Literal["low", "medium", "high"]]],
        """Filter by urgency level(s): list of 'low', 'medium', or 'high'. 
        Can specify one or more urgencies (e.g., ["high"], ["high", "medium"]).
        If not provided, all urgencies are included."""
    ] = None,
    tags: Annotated[
        Optional[list[str]],
        """Filter by tags (tasks must have all specified tags).
        Can specify one or more tags (e.g., ["tag1"], ["tag1", "tag2"]).
        If not provided, all tags are included."""
    ] = None,
    assignee: Annotated[
        Optional[str],
        "Filter by assignee name. If not provided, all assignees are included.",
    ] = None,
    due_before: Annotated[
        Optional[str],
        "Filter tasks due before this date (ISO 8601 format). If not provided, all due dates are included.",
    ] = None,
    due_after: Annotated[
        Optional[str],
        "Filter tasks due after this date (ISO 8601 format). If not provided, all due dates are included.",
    ] = None,
    page: Annotated[
        int,
        "Page number for pagination (starts at 1). Default is 1.",
    ] = 1,
    page_size: Annotated[
        int,
        "Number of tasks per page. Default is 20.",
    ] = 20,
    include_description: Annotated[
        bool,
        "Whether to include task descriptions in results. Default is True.",
    ] = True,
) -> dict:
    """List active tasks from the GitHub repository with filtering, sorting, and pagination.

    Returns tasks sorted by priority (high to low) and due date (earliest first). Supports
    filtering by status, priority, urgency, tags, assignee, and date ranges. By default, task
    descriptions are excluded for brevity; set include_description=true to include them.

    Example: List all high-priority open tasks:
    status=["open"], priority=["high"]

    Example: List tasks with multiple statuses and priorities:
    status=["open", "in-progress"], priority=["high", "medium"]

    Example: List high-urgency tasks:
    urgency=["high"]

    Example: List tasks due next week:
    due_after="2026-01-10T00:00:00Z", due_before="2026-01-17T23:59:59Z", include_description=True

    Example: List tasks by assignee with pagination:
    assignee="John", page=1, page_size=10
    """
    try:
        files = client.read_directory_files("tasks")
    except RuntimeError as exc:
        detail = str(exc)
        if "not a directory" in detail.lower() or "not found" in detail.lower():
            logger.info("tasks/ directory missing; returning empty list")
            return {"total": 0, "page": 1, "page_size": page_size, "tasks": []}
        raise
    tasks: List[dict] = []
    for f in files:
        data = yaml.safe_load(f.content) or {}
        try:
            task = TaskPayload(**data)
        except ValidationError:
            logger.warning("Skipping invalid task file", extra={"path": f.path})
            continue
        if status and task.status not in status:
            continue
        if priority and task.priority not in priority:
            continue
        if urgency and task.urgency not in urgency:
            continue
        if assignee and task.assignee != assignee:
            continue
        if tags:
            if not set(tags).issubset(set(task.tags)):
                continue
        if due_before or due_after:
            if not task.due_date:
                continue
            dt = _parse_iso(task.due_date)
            if dt is None:
                continue
            if due_before:
                before_dt = _parse_iso(due_before)
                if before_dt and dt > before_dt:
                    continue
            if due_after:
                after_dt = _parse_iso(due_after)
                if after_dt and dt < after_dt:
                    continue
        task_dict = task.dict()
        if not include_description:
            task_dict.pop("description", None)
        tasks.append({"filename": f.path, "task": task_dict})

    def _sort_key(item: dict) -> tuple[int, datetime]:
        due_str = item["task"].get("due_date")
        due_dt = _parse_iso(due_str) if due_str else None
        fallback = datetime.max.replace(tzinfo=UTC)
        return (
            _priority_order(item["task"].get("priority", "medium")),
            due_dt or fallback,
        )

    tasks.sort(key=_sort_key)

    page_val = max(1, page)
    size = max(1, min(100, page_size))
    start = (page_val - 1) * size
    end = start + size
    return {
        "total": len(tasks),
        "page": page_val,
        "page_size": size,
        "tasks": tasks[start:end],
    }


# ---------------------------------------------------------------------------
# Reminder management tools (communicate with reminder_cli daemon)


def _run_reminder_cli(args: List[str]) -> tuple[str, int]:
    """Run the reminder CLI command and return (output, exit_code)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "todo_list_mcp.reminder_cli"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "Command timed out", 1
    except Exception as e:
        return f"Error running command: {e}", 1


def _ensure_daemon_running() -> None:
    """Ensure reminder daemon is running. Start it if not already running."""
    # Check daemon status
    output, code = _run_reminder_cli(["status"])

    if code == 0:
        # Daemon is already running
        logger.info("Reminder daemon is already running")
        return

    # Start the daemon in background
    logger.info("Starting reminder daemon...")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "todo_list_mcp.reminder_cli", "daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent process
        )
        # Give it a moment to start
        import time

        time.sleep(0.5)

        # Verify it started
        output, code = _run_reminder_cli(["status"])
        if code == 0:
            logger.info("Reminder daemon started successfully")
        else:
            logger.warning("Reminder daemon may not have started properly")
    except Exception as e:
        logger.error(f"Failed to start reminder daemon: {e}")


@app.tool()
def set_reminders(
    reminders: Annotated[
        List[dict],
        """List of reminders to set. Each reminder is a dictionary with the following schema:
        
        Required fields:
        - title (str): Reminder title or name
        - message (str): Reminder message or description
        - due_at (str): Due date/time in ISO 8601 format (e.g., "2026-01-15T10:00:00Z")
        
        Optional fields:
        - task_filename (str | None): Task filename to associate with this reminder (e.g., "tasks/review-pr-abc123.yaml"). 
          If not provided, uses the request-level task_filename parameter if set.
        
        Example reminder dict:
        {
            "title": "Review PR",
            "message": "Review the pull request for the new feature",
            "due_at": "2026-01-15T14:00:00Z",
            "task_filename": "tasks/review-pr-abc123.yaml"
        }
        
        Example reminder dict without task association:
        {
            "title": "Team Meeting",
            "message": "Daily standup at 10 AM",
            "due_at": "2026-01-15T10:00:00Z"
        }""",
    ],
    task_filename: Annotated[
        Optional[str],
        "Optional task filename to associate with all reminders (can be overridden per reminder)",
    ] = None,
) -> dict:
    """Set one or more reminders via the reminder daemon.

    Each reminder must specify a title, message, and due_at timestamp in ISO 8601 format.
    Optionally, you can link reminders to a task by providing task_filename at the request level
    (applies to all reminders) or per individual reminder (overrides request-level setting).
    The reminders will be stored persistently and delivered by the reminder daemon.

    Example: Set a single reminder:
    reminders=[{"title": "Meeting", "message": "Team standup", "due_at": "2026-01-15T10:00:00Z"}]

    Example: Set a reminder linked to a task:
    reminders=[{"title": "Review PR", "message": "Review the pull request", "due_at": "2026-01-15T14:00:00Z"}], task_filename="tasks/review-pr-abc123.yaml"

    Example: Set multiple reminders for the same task:
    reminders=[
        {"title": "Start work", "message": "Begin code review", "due_at": "2026-01-15T09:00:00Z"},
        {"title": "Follow up", "message": "Check review status", "due_at": "2026-01-15T17:00:00Z"}
    ], task_filename="tasks/review-pr-abc123.yaml"

    Example: Set multiple reminders with mixed task associations:
    reminders=[
        {"title": "Task A reminder", "message": "Work on A", "due_at": "2026-01-15T10:00:00Z", "task_filename": "tasks/task-a.yaml"},
        {"title": "Task B reminder", "message": "Work on B", "due_at": "2026-01-15T14:00:00Z", "task_filename": "tasks/task-b.yaml"}
    ]
    """
    results = []
    for reminder in reminders:
        title = reminder.get("title", "Reminder")
        message = reminder.get("message", "")
        due_at = reminder.get("due_at", "")
        # Use reminder-specific task_filename, fall back to request-level one
        reminder_task_filename = reminder.get("task_filename") or task_filename

        if not due_at:
            results.append({"error": "Missing due_at timestamp", "reminder": reminder})
            continue

        args = ["add", title, message, due_at]
        if reminder_task_filename:
            args.extend(["--task", reminder_task_filename])
        output, code = _run_reminder_cli(args)
        if code == 0:
            # Extract ID from output if possible
            reminder_id = "unknown"
            for line in output.split("\n"):
                if "Reminder added:" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        reminder_id = parts[-1].strip()
            results.append({"status": "added", "id": reminder_id})
        else:
            results.append({"error": output, "reminder": reminder})

    return {"results": results}


@app.tool()
def list_reminders() -> dict:
    """List all reminders stored in the reminder daemon.

    Returns a list of all pending and due reminders with their details including
    ID, title, message, due time, and current status.

    Example: {}
    """
    output, code = _run_reminder_cli(["list"])
    if code != 0:
        return {"error": output}

    # Parse the output - for simplicity, return raw output
    # In production, you might want to parse the table format
    return {"output": output, "status": "success"}


@app.tool()
def remove_reminders(
    ids: Annotated[
        Optional[List[str]],
        "List of reminder IDs to remove",
    ] = None,
    all: Annotated[
        bool,
        "If true, remove all reminders (ignores 'ids' field)",
    ] = False,
) -> dict:
    """Remove one or more reminders from the reminder daemon.

    You can either specify specific reminder IDs to remove, or use the 'all' flag
    to remove all reminders at once.

    Example: Remove specific reminders:
    ids=["abc123", "def456"]

    Example: Remove all reminders:
    all=True
    """
    if all:
        output, code = _run_reminder_cli(["remove", "--all"])
    elif ids:
        output, code = _run_reminder_cli(["remove"] + ids)
    else:
        return {"error": "Must specify either 'ids' or set 'all' to true"}

    if code == 0:
        return {"status": "success", "output": output}
    else:
        return {"error": output}


# ---------------------------------------------------------------------------
# Serialization


def _serialize_task(task: TaskPayload) -> str:
    payload: Dict[str, Any] = task.dict()
    # Ensure stable ordering for readability.
    ordered_keys = [
        "title",
        "description",
        "status",
        "priority",
        "urgency",
        "time_estimate",
        "due_date",
        "tags",
        "assignee",
        "created_at",
        "updated_at",
    ]
    ordered = {k: payload.get(k) for k in ordered_keys if k in payload}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=False)


def main() -> None:
    logger.info("Starting todo-list MCP server")
    _ensure_daemon_running()
    app.run(show_banner=False)


if __name__ == "__main__":
    main()
