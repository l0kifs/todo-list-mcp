"""Todo List MCP server single-file entrypoint.

Provides create/read/update/list operations over tasks stored in
a local SQLite database.

Reminder management is handled via the standalone reminder_cli daemon,
which is automatically started when the MCP server starts (if not already running).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from typing import Annotated, List, Literal, Optional

from fastmcp import FastMCP
from loguru import logger
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from todo_list_mcp.logging_config import setup_logging
from todo_list_mcp.models import Base, Task
from todo_list_mcp.settings import get_settings
from todo_list_mcp.sqlite_client import SQLiteClient

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


# ---------------------------------------------------------------------------
# MCP server setup

settings = get_settings()
setup_logging(settings)

# Initialize SQLite client and create tables
db_client = SQLiteClient(database_url=settings.database_url)
db_client.ensure_database_exists()
db_client.create_tables(Base)

logger.info(
    "SQLite database initialized",
    database_url=settings.database_url,
)

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
        "Deprecated parameter (kept for backward compatibility). Filenames are no longer used with SQLite storage.",
    ] = None,
) -> dict:
    """Create one or more tasks in the SQLite database.

    Tasks are stored in the database with auto-generated IDs. Timestamps are
    automatically managed.

    Example: Create a single task:
    tasks=[{"title": "Review PR", "priority": "high", "urgency": "high", "time_estimate": 2.5, "due_date": "2026-01-15T10:00:00Z"}]

    Example: Create multiple tasks:
    tasks=[{"title": "Task 1"}, {"title": "Task 2"}]
    """
    task_objects = [TaskPayload(**task) for task in tasks]
    created_ids: List[int] = []

    with db_client.transaction() as session:
        for task_payload in task_objects:
            now_iso = _now_iso()
            task_payload.created_at = task_payload.created_at or now_iso
            task_payload.updated_at = now_iso

            # Create Task ORM instance
            task_orm = Task(
                title=task_payload.title,
                description=task_payload.description,
                status=task_payload.status,
                priority=task_payload.priority,
                urgency=task_payload.urgency,
                time_estimate=task_payload.time_estimate,
                due_date=task_payload.due_date,
                tags=task_payload.tags or [],
                assignee=task_payload.assignee,
                created_at=task_payload.created_at,
                updated_at=task_payload.updated_at,
            )
            db_client.add(session, task_orm)
            session.flush()  # Flush to get the ID
            created_ids.append(task_orm.id)

            logger.info(
                "Created task",
                task_id=task_orm.id,
                title=task_orm.title,
                status=task_orm.status,
            )

    return {"created": created_ids, "count": len(created_ids)}


@app.tool()
def read_tasks(
    filenames: Annotated[
        Optional[List[str]],
        "Deprecated parameter (kept for backward compatibility). Use task IDs instead. Can pass strings like '1', '2', '3' as task IDs.",
    ] = None,
    ids: Annotated[
        Optional[List[int]],
        "List of task IDs to read (e.g., [1, 2, 3]). Returns complete task data including all fields and metadata.",
    ] = None,
) -> dict:
    """Read one or more tasks from the SQLite database by ID.

    Returns complete task data including all fields and metadata. Use this to retrieve
    task details for review or before updating.

    Example: ids=[1, 2, 3]
    Example (backward compatibility): filenames=["1", "2", "3"]
    """
    # Support backward compatibility: convert filenames to IDs
    task_ids: List[int] = []
    if ids:
        task_ids.extend(ids)
    if filenames:
        for fn in filenames:
            # Try to extract numeric ID from filename
            try:
                # Handle formats like "1", "task-1.yaml", "tasks/1.yaml"
                id_str = (
                    fn.replace("tasks/", "")
                    .replace("archive/", "")
                    .replace(".yaml", "")
                    .replace("task-", "")
                )
                task_id = int(id_str)
                task_ids.append(task_id)
            except (ValueError, AttributeError):
                logger.warning(f"Could not parse task ID from filename: {fn}")

    if not task_ids:
        return {"tasks": [], "error": "No valid task IDs provided"}

    results = []
    with db_client.session() as session:
        for task_id in task_ids:
            task = db_client.get_by_id(session, Task, task_id)
            if task:
                results.append(
                    {
                        "id": task.id,
                        "task": task.to_dict(),
                    }
                )
            else:
                logger.warning(f"Task not found: {task_id}")

    return {"tasks": results, "count": len(results)}


@app.tool()
def update_tasks(
    updates: Annotated[
        List[dict],
        """List of updates, each containing 'id' (or 'filename' for backward compatibility) and fields to update. Only provided fields are updated; others remain unchanged.
        
        Required fields:
        - id (int): Task ID to update (e.g., 1, 2, 3)
        OR
        - filename (str): Deprecated, but supported for backward compatibility (e.g., "1", "task-1.yaml")
        
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
            "id": 1,
            "status": "done",
            "priority": "high",
            "urgency": "low",
            "assignee": "John Doe"
        }""",
    ],
) -> dict:
    """Update one or more existing tasks in the SQLite database.

    Each update must specify an ID and the fields to modify. Only provided fields
    are updated; others remain unchanged. The updated_at timestamp is automatically set.

    Example: Mark a task as done and change priority:
    updates=[{"id": 1, "status": "done", "priority": "high", "urgency": "low"}]

    Example: Update multiple tasks:
    updates=[{"id": 1, "status": "in-progress", "time_estimate": 3.0}, {"id": 2, "assignee": "John"}]
    """
    updated_ids: List[int] = []

    with db_client.transaction() as session:
        for item in updates:
            # Support both 'id' and 'filename' for backward compatibility
            task_id = item.get("id")
            if task_id is None and "filename" in item:
                # Try to extract ID from filename
                try:
                    filename = item["filename"]
                    id_str = (
                        filename.replace("tasks/", "")
                        .replace("archive/", "")
                        .replace(".yaml", "")
                        .replace("task-", "")
                    )
                    task_id = int(id_str)
                except (ValueError, AttributeError):
                    logger.warning(
                        f"Could not parse task ID from filename: {item.get('filename')}"
                    )
                    continue

            if task_id is None:
                logger.warning("Update item missing 'id' field")
                continue

            task = db_client.get_by_id(session, Task, task_id)
            if not task:
                logger.warning(f"Task not found: {task_id}")
                continue

            # Update fields
            for key, value in item.items():
                if key in ("id", "filename"):
                    continue
                if hasattr(task, key):
                    setattr(task, key, value)

            # Auto-update timestamp
            task.updated_at = _now_iso()
            updated_ids.append(task_id)

            logger.info(
                "Updated task",
                task_id=task_id,
                updated_fields=list(item.keys()),
            )

    return {"updated": updated_ids, "count": len(updated_ids)}


@app.tool()
def delete_tasks(
    ids: Annotated[
        List[int],
        "List of task IDs to delete (e.g., [1, 2, 3]). Tasks are permanently removed from the database.",
    ],
) -> dict:
    """Delete one or more tasks from the SQLite database by ID.

    Tasks are permanently removed from the database. This operation cannot be undone.
    Use this to remove tasks that are no longer needed.

    Example: Delete single task:
    ids=[1]

    Example: Delete multiple tasks:
    ids=[1, 2, 3]
    """
    deleted_ids: List[int] = []
    not_found_ids: List[int] = []

    with db_client.transaction() as session:
        for task_id in ids:
            task = db_client.get_by_id(session, Task, task_id)
            if task:
                db_client.delete(session, task)
                deleted_ids.append(task_id)
                logger.info("Deleted task", task_id=task_id, title=task.title)
            else:
                not_found_ids.append(task_id)
                logger.warning(f"Task not found: {task_id}")

    result = {
        "deleted": deleted_ids,
        "count": len(deleted_ids),
    }
    if not_found_ids:
        result["not_found"] = not_found_ids

    return result


@app.tool()
def list_tasks(
    status: Annotated[
        Optional[list[Literal["open", "in-progress", "done"]]],
        """Filter by status(es): list of 'open', 'in-progress', or 'done'. 
        Can specify one or more statuses (e.g., ["open"], ["open", "in-progress"]).
        If not provided, all statuses are included.""",
    ] = None,
    priority: Annotated[
        Optional[list[Literal["low", "medium", "high"]]],
        """Filter by priority(ies): list of 'low', 'medium', or 'high'. 
        Can specify one or more priorities (e.g., ["high"], ["high", "medium"]).
        If not provided, all priorities are included.""",
    ] = None,
    urgency: Annotated[
        Optional[list[Literal["low", "medium", "high"]]],
        """Filter by urgency level(s): list of 'low', 'medium', or 'high'. 
        Can specify one or more urgencies (e.g., ["high"], ["high", "medium"]).
        If not provided, all urgencies are included.""",
    ] = None,
    tags: Annotated[
        Optional[list[str]],
        """Filter by tags (tasks must have all specified tags).
        Can specify one or more tags (e.g., ["tag1"], ["tag1", "tag2"]).
        If not provided, all tags are included.""",
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
    """List tasks from the SQLite database with filtering, sorting, and pagination.

    Returns tasks sorted by priority (high to low) and due date (earliest first). Supports
    filtering by status, priority, urgency, tags, assignee, and date ranges.

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
    with db_client.session() as session:
        # Build query with filters
        stmt = select(Task)

        # Filter by status
        if status:
            stmt = stmt.where(Task.status.in_(status))

        # Filter by priority
        if priority:
            stmt = stmt.where(Task.priority.in_(priority))

        # Filter by urgency
        if urgency:
            stmt = stmt.where(Task.urgency.in_(urgency))

        # Filter by assignee
        if assignee:
            stmt = stmt.where(Task.assignee == assignee)

        # Filter by due date
        if due_before:
            before_dt = _parse_iso(due_before)
            if before_dt:
                stmt = stmt.where(Task.due_date <= due_before)

        if due_after:
            after_dt = _parse_iso(due_after)
            if after_dt:
                stmt = stmt.where(Task.due_date >= due_after)

        # Execute query
        all_tasks = session.scalars(stmt).all()

        # Filter by tags (must have all specified tags) - done in Python since JSON filtering is complex
        filtered_tasks = []
        for task in all_tasks:
            if tags:
                task_tags = set(task.tags or [])
                required_tags = set(tags)
                if not required_tags.issubset(task_tags):
                    continue
            filtered_tasks.append(task)

        # Sort by priority and due date
        def _sort_key(task: Task) -> tuple:
            due_dt = _parse_iso(task.due_date) if task.due_date else None
            fallback = datetime.max.replace(tzinfo=UTC)
            return (
                _priority_order(task.priority),
                due_dt or fallback,
            )

        filtered_tasks.sort(key=_sort_key)

        # Paginate
        page_val = max(1, page)
        size = max(1, min(100, page_size))
        start = (page_val - 1) * size
        end = start + size
        page_tasks = filtered_tasks[start:end]

        # Convert to dict format
        results = []
        for task in page_tasks:
            task_dict = task.to_dict()
            if not include_description:
                task_dict.pop("description", None)
            results.append(
                {
                    "id": task.id,
                    "task": task_dict,
                }
            )

        return {
            "total": len(filtered_tasks),
            "page": page_val,
            "page_size": size,
            "tasks": results,
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
# Main entry point


def main() -> None:
    logger.info("Starting todo-list MCP server with SQLite storage")
    _ensure_daemon_running()
    app.run(show_banner=False)


if __name__ == "__main__":
    main()
