"""Happy-path end-to-end tests calling the running MCP server via SyncMCPClient.

These tests exercise the public tools exactly as a production client would.
They require GitHub credentials with write access to the configured repository.
Relies on settings from the server configuration (Settings) being present in
the environment or .env; no skipping based on env checks here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.exceptions import ToolError

from tests.client import SyncMCPClient

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def mcp_client():
    """Starts the MCP server via SyncMCPClient using current environment."""
    import os

    server_path = os.path.abspath(__file__)
    with SyncMCPClient(server_path, env=dict(os.environ)) as client:
        yield client


@pytest.fixture()
def unique_task_name() -> str:
    return f"e2e-{uuid.uuid4().hex[:8]}.yaml"


def _base_payload(unique: str) -> dict:
    due = (datetime.now(tz=UTC) + timedelta(days=2)).isoformat()
    return {
        "title": f"E2E Task {unique}",
        "description": "happy path",
        "status": "open",
        "priority": "medium",
        "urgency": "medium",
        "time_estimate": 2.5,
        "due_date": due,
        "tags": ["e2e"],
        "assignee": "bot",
        "reminders": [],
    }


def _unwrap(result):
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return structured
    # Fallback: try to parse text content
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return text
    return result


def test_create_and_read_task(mcp_client: SyncMCPClient, unique_task_name: str) -> None:
    payload = _base_payload(unique_task_name)
    result = mcp_client.call_tool(
        "create_tasks",
        {"body": {"tasks": [payload], "filenames": [unique_task_name]}},
    )
    created_paths = _unwrap(result)["created"]
    assert len(created_paths) == 1
    assert created_paths[0].endswith(unique_task_name)

    read_res = mcp_client.call_tool(
        "read_tasks", {"body": {"filenames": [unique_task_name]}}
    )
    read_data = _unwrap(read_res)
    assert read_data["tasks"][0]["task"]["title"] == payload["title"]


def test_update_task(mcp_client: SyncMCPClient, unique_task_name: str) -> None:
    payload = _base_payload(unique_task_name)
    try:
        mcp_client.call_tool(
            "create_tasks",
            {"body": {"tasks": [payload], "filenames": [unique_task_name]}},
        )
    except ToolError as exc:
        if "fast forward" in str(exc).lower():
            pytest.skip("Branch protection or out-of-date ref prevents writes")
        raise

    updated = mcp_client.call_tool(
        "update_tasks",
        {
            "body": {
                "updates": [
                    {
                        "filename": unique_task_name,
                        "status": "done",
                        "priority": "high",
                        "urgency": "low",
                        "time_estimate": 1.0,
                        "description": "done and high",
                    }
                ]
            }
        },
    )
    updated_data = _unwrap(updated)
    assert any(path.endswith(unique_task_name) for path in updated_data["updated"])

    read_res = mcp_client.call_tool(
        "read_tasks", {"body": {"filenames": [unique_task_name]}}
    )
    task = _unwrap(read_res)["tasks"][0]["task"]
    assert task["status"] == "done"
    assert task["priority"] == "high"
    assert task["urgency"] == "low"
    assert task["time_estimate"] == 1.0


def test_list_filters_and_sort(
    mcp_client: SyncMCPClient, unique_task_name: str
) -> None:
    payload = _base_payload(unique_task_name)
    mcp_client.call_tool(
        "create_tasks",
        {"body": {"tasks": [payload], "filenames": [unique_task_name]}},
    )

    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "body": {
                "status": "open",
                "include_description": True,
                "page": 1,
                "page_size": 100,
            }
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["filename"].endswith(unique_task_name)]
    assert listed, "Task not found in list"
    assert listed[0]["task"]["title"] == payload["title"]


def test_archive_task(mcp_client: SyncMCPClient, unique_task_name: str) -> None:
    payload = _base_payload(unique_task_name)
    try:
        mcp_client.call_tool(
            "create_tasks",
            {"body": {"tasks": [payload], "filenames": [unique_task_name]}},
        )
    except ToolError as exc:
        msg = str(exc)
        if "fast forward" in msg.lower():
            pytest.skip("Branch protection or out-of-date ref prevents writes")
        raise

    arch_res = mcp_client.call_tool(
        "archive_tasks", {"body": {"filenames": [unique_task_name]}}
    )
    arch_data = _unwrap(arch_res)
    assert any(path.endswith(unique_task_name) for path in arch_data["archived"])

    list_after = mcp_client.call_tool(
        "list_tasks",
        {
            "body": {
                "status": "open",
                "include_description": False,
                "page": 1,
                "page_size": 100,
            }
        },
    )
    after_data = _unwrap(list_after)
    listed_after = [
        t for t in after_data["tasks"] if t["filename"].endswith(unique_task_name)
    ]
    assert not listed_after


def test_reminder_tools_happy_path(mcp_client: SyncMCPClient) -> None:
    due_at = (datetime.now(tz=UTC) + timedelta(minutes=2)).isoformat()
    reminder_title = f"e2e-reminder-{uuid.uuid4().hex[:8]}"

    set_res = mcp_client.call_tool(
        "set_reminders",
        {
            "body": {
                "reminders": [
                    {
                        "title": reminder_title,
                        "message": "test reminder",
                        "due_at": due_at,
                    }
                ]
            }
        },
    )
    set_data = _unwrap(set_res)
    first = set_data["results"][0]
    assert first["status"] == "added"
    reminder_id = first["id"]
    assert reminder_id != "unknown"

    list_res = mcp_client.call_tool("list_reminders", {})
    list_data = _unwrap(list_res)
    assert list_data["status"] == "success"
    assert reminder_id in list_data["output"]

    remove_res = mcp_client.call_tool(
        "remove_reminders", {"body": {"ids": [reminder_id]}}
    )
    remove_data = _unwrap(remove_res)
    assert remove_data["status"] == "success"

    list_after = mcp_client.call_tool("list_reminders", {})
    list_after_data = _unwrap(list_after)
    if list_after_data.get("status") == "success":
        assert reminder_id not in list_after_data["output"]
