"""Todo List MCP server single-file entrypoint.

Provides create/read/update/archive/list operations over YAML tasks stored in
GitHub under the tasks/ directory. Archives move files into archive/. Also
launches a lightweight reminder sidecar that uses the existing reminder_client
and sound_client to show desktop reminders and optional beeps for reminder
timestamps recorded on tasks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from fastmcp import FastMCP
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, root_validator

from todo_list_mcp.github_file_client import GitHubFileClient
from todo_list_mcp.logging_config import setup_logging
from todo_list_mcp.reminder_sidecar import ReminderSidecar
from todo_list_mcp.settings import get_settings

# ---------------------------------------------------------------------------
# Models

Status = Literal["open", "in-progress", "done"]
Priority = Literal["low", "medium", "high"]


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
    reminders: List[str] = Field(
        default_factory=list,
        description="List of reminder timestamps in ISO 8601 format",
    )

    @root_validator(pre=True)
    def normalize_lists(cls, values: dict) -> dict:  # type: ignore[override]
        tags = values.get("tags")
        reminders = values.get("reminders")
        if tags is None:
            values["tags"] = []
        if reminders is None:
            values["reminders"] = []
        return values


class CreateTaskRequest(BaseModel):
    tasks: List[TaskPayload] = Field(description="List of tasks to create")
    filenames: Optional[List[str]] = Field(
        None,
        description="Optional explicit filenames for tasks (e.g., ['my-task.yaml']). If not provided, filenames are auto-generated from titles",
    )


class ReadTaskRequest(BaseModel):
    filenames: List[str] = Field(
        description="List of task filenames to read (e.g., ['task-abc123.yaml', 'my-task.yaml'])"
    )


class UpdateTaskRequest(BaseModel):
    updates: List[dict] = Field(
        description="List of updates, each containing 'filename' and fields to update (e.g., [{'filename': 'task.yaml', 'status': 'done', 'priority': 'high'}])"
    )


class ArchiveTaskRequest(BaseModel):
    filenames: List[str] = Field(
        description="List of task filenames to archive (e.g., ['completed-task.yaml']). Tasks are moved to archive/ directory"
    )


class ListTaskRequest(BaseModel):
    status: Optional[Status] = Field(
        None, description="Filter by status: 'open', 'in-progress', or 'done'"
    )
    priority: Optional[Priority] = Field(
        None, description="Filter by priority: 'low', 'medium', or 'high'"
    )
    tags: Optional[List[str]] = Field(
        None, description="Filter by tags (tasks must have all specified tags)"
    )
    assignee: Optional[str] = Field(None, description="Filter by assignee name")
    due_before: Optional[str] = Field(
        None, description="Filter tasks due before this date (ISO 8601 format)"
    )
    due_after: Optional[str] = Field(
        None, description="Filter tasks due after this date (ISO 8601 format)"
    )
    page: int = Field(1, description="Page number for pagination (starts at 1)", ge=1)
    page_size: int = Field(20, description="Number of tasks per page", ge=1, le=100)
    include_description: bool = Field(
        False, description="Whether to include task descriptions in results"
    )


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

client = GitHubFileClient(
    owner=settings.github_repo_owner,
    repo=settings.github_repo_name,
    token=settings.github_api_token,
)

sidecar = ReminderSidecar()

app = FastMCP("todo-list-mcp")


# ---------------------------------------------------------------------------
# Tools


@app.tool()
def create_tasks(body: CreateTaskRequest) -> dict:
    """Create one or more tasks in the GitHub repository.

    Tasks are stored as YAML files in the tasks/ directory. Each task is automatically
    assigned timestamps and can include reminders for desktop notifications.

    Example: Create a single task with default filename:
    {"tasks": [{"title": "Review PR", "priority": "high", "due_date": "2026-01-15T10:00:00Z"}]}

    Example: Create multiple tasks with custom filenames:
    {"tasks": [{"title": "Task 1"}, {"title": "Task 2"}], "filenames": ["task1.yaml", "task2.yaml"]}
    """
    tasks = body.tasks
    filenames = body.filenames or []
    outputs = []
    file_pairs: List[tuple[str, str]] = []
    for idx, task in enumerate(tasks):
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
        sidecar.schedule_from_task(full_path, task)
    created = client.create_files(file_pairs)
    return {"created": [c.path for c in created]}


@app.tool()
def read_tasks(body: ReadTaskRequest) -> dict:
    """Read one or more tasks from the GitHub repository by filename.

    Returns complete task data including all fields and metadata. Use this to retrieve
    task details for review or before updating.

    Example: {"filenames": ["review-pr-abc123.yaml", "fix-bug-def456.yaml"]}
    """
    results = []
    for name in body.filenames:
        path = _task_path(name)
        file = client.read_file(path)
        task = yaml.safe_load(file.content) or {}
        results.append({"filename": path, "task": task, "sha": file.sha})
    return {"tasks": results}


@app.tool()
def update_tasks(body: UpdateTaskRequest) -> dict:
    """Update one or more existing tasks in the GitHub repository.

    Each update must specify a filename and the fields to modify. Only provided fields
    are updated; others remain unchanged. The updated_at timestamp is automatically set.

    Example: Mark a task as done and change priority:
    {"updates": [{"filename": "review-pr.yaml", "status": "done", "priority": "high"}]}

    Example: Update multiple tasks:
    {"updates": [{"filename": "task1.yaml", "status": "in-progress"}, {"filename": "task2.yaml", "assignee": "John"}]}
    """
    updated_paths: List[str] = []
    for item in body.updates:
        filename = _task_path(item.get("filename"))
        existing = client.read_file(filename)
        data = yaml.safe_load(existing.content) or {}
        merged = {**data, **{k: v for k, v in item.items() if k != "filename"}}
        merged.setdefault("created_at", data.get("created_at") or _now_iso())
        merged["updated_at"] = _now_iso()
        task_obj = TaskPayload(**merged)
        content = _serialize_task(task_obj)
        client.update_file(filename, content, sha=existing.sha)
        updated_paths.append(filename)
        sidecar.schedule_from_task(filename, task_obj)
    return {"updated": updated_paths}


@app.tool()
def archive_tasks(body: ArchiveTaskRequest) -> dict:
    """Archive completed or obsolete tasks by moving them to the archive/ directory.

    Archived tasks are moved from tasks/ to archive/ in the GitHub repository, removing
    them from active task lists while preserving their data for future reference.

    Example: {"filenames": ["completed-task.yaml", "old-task.yaml"]}
    """
    archived: List[str] = []
    for name in body.filenames:
        src = _task_path(name)
        tgt_name = Path(name).name
        tgt = f"archive/{tgt_name}"
        client.move_file(src, tgt)
        archived.append(tgt)
    return {"archived": archived}


@app.tool()
def list_tasks(body: ListTaskRequest) -> dict:
    """List active tasks from the GitHub repository with filtering, sorting, and pagination.

    Returns tasks sorted by priority (high to low) and due date (earliest first). Supports
    filtering by status, priority, tags, assignee, and date ranges. By default, task
    descriptions are excluded for brevity; set include_description=true to include them.

    Example: List all high-priority open tasks:
    {"status": "open", "priority": "high"}

    Example: List tasks due next week:
    {"due_after": "2026-01-10T00:00:00Z", "due_before": "2026-01-17T23:59:59Z", "include_description": true}

    Example: List tasks by assignee with pagination:
    {"assignee": "John", "page": 1, "page_size": 10}
    """
    try:
        files = client.read_directory_files("tasks")
    except RuntimeError as exc:
        detail = str(exc)
        if "not a directory" in detail.lower() or "not found" in detail.lower():
            logger.info("tasks/ directory missing; returning empty list")
            return {"total": 0, "page": 1, "page_size": body.page_size, "tasks": []}
        raise
    tasks: List[dict] = []
    for f in files:
        data = yaml.safe_load(f.content) or {}
        try:
            task = TaskPayload(**data)
        except ValidationError:
            logger.warning("Skipping invalid task file", extra={"path": f.path})
            continue
        if body.status and task.status != body.status:
            continue
        if body.priority and task.priority != body.priority:
            continue
        if body.assignee and task.assignee != body.assignee:
            continue
        if body.tags:
            if not set(body.tags).issubset(set(task.tags)):
                continue
        if body.due_before or body.due_after:
            if not task.due_date:
                continue
            dt = _parse_iso(task.due_date)
            if dt is None:
                continue
            if body.due_before:
                before_dt = _parse_iso(body.due_before)
                if before_dt and dt > before_dt:
                    continue
            if body.due_after:
                after_dt = _parse_iso(body.due_after)
                if after_dt and dt < after_dt:
                    continue
        task_dict = task.dict()
        if not body.include_description:
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

    page = max(1, body.page)
    size = max(1, min(100, body.page_size))
    start = (page - 1) * size
    end = start + size
    return {
        "total": len(tasks),
        "page": page,
        "page_size": size,
        "tasks": tasks[start:end],
    }


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
        "due_date",
        "tags",
        "assignee",
        "created_at",
        "updated_at",
        "reminders",
    ]
    ordered = {k: payload.get(k) for k in ordered_keys if k in payload}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=False)


# ---------------------------------------------------------------------------
# Startup: preload reminders from existing tasks


def _bootstrap_reminders() -> None:
    try:
        files = client.read_directory_files("tasks")
    except Exception as exc:
        detail = str(exc)
        if "not a directory" in detail.lower() or "not found" in detail.lower():
            logger.info("tasks/ directory missing; skipping reminder bootstrap")
            return
        logger.warning("Could not bootstrap reminders", error=detail)
        return
    for f in files:
        try:
            data = yaml.safe_load(f.content) or {}
            task = TaskPayload(**data)
        except Exception:
            continue
        sidecar.schedule_from_task(f.path, task)


def main() -> None:
    logger.info("Starting todo-list MCP server")
    _bootstrap_reminders()
    app.run()


if __name__ == "__main__":
    main()
