"""Happy-path end-to-end tests calling the running MCP server via SyncMCPClient.

These tests exercise the public tools exactly as a production client would.
They require GitHub credentials with write access to the configured repository.
Relies on settings from the server configuration (Settings) being present in
the environment or .env; no skipping based on env checks here.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotenv import load_dotenv
from fastmcp.exceptions import ToolError

from tests.client import SyncMCPClient

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def mcp_client():
    """Starts the MCP server via SyncMCPClient using current environment."""

    # Load .env file if it exists
    load_dotenv()

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
        {"tasks": [payload]},
    )
    created_ids = _unwrap(result)["created"]
    assert len(created_ids) == 1
    assert isinstance(created_ids[0], int)
    task_id = created_ids[0]

    read_res = mcp_client.call_tool("read_tasks", {"ids": [task_id]})
    read_data = _unwrap(read_res)
    assert read_data["tasks"][0]["task"]["title"] == payload["title"]


def test_update_task(mcp_client: SyncMCPClient, unique_task_name: str) -> None:
    payload = _base_payload(unique_task_name)
    try:
        result = mcp_client.call_tool(
            "create_tasks",
            {"tasks": [payload]},
        )
        task_id = _unwrap(result)["created"][0]
    except ToolError as exc:
        if "fast forward" in str(exc).lower():
            pytest.skip("Branch protection or out-of-date ref prevents writes")
        raise

    updated = mcp_client.call_tool(
        "update_tasks",
        {
            "updates": [
                {
                    "id": task_id,
                    "status": "done",
                    "priority": "high",
                    "urgency": "low",
                    "time_estimate": 1.0,
                    "description": "done and high",
                }
            ]
        },
    )
    updated_data = _unwrap(updated)
    assert task_id in updated_data["updated"]

    read_res = mcp_client.call_tool("read_tasks", {"ids": [task_id]})
    task = _unwrap(read_res)["tasks"][0]["task"]
    assert task["status"] == "done"
    assert task["priority"] == "high"
    assert task["urgency"] == "low"
    assert task["time_estimate"] == 1.0


def test_delete_tasks(mcp_client: SyncMCPClient, unique_task_name: str) -> None:
    """Test deleting single and multiple tasks."""
    payload1 = _base_payload(unique_task_name + "-1")
    payload2 = _base_payload(unique_task_name + "-2")
    
    # Create two tasks
    result = mcp_client.call_tool(
        "create_tasks",
        {"tasks": [payload1, payload2]},
    )
    created_ids = _unwrap(result)["created"]
    assert len(created_ids) == 2
    task_id_1, task_id_2 = created_ids[0], created_ids[1]

    # Delete first task
    delete_res = mcp_client.call_tool("delete_tasks", {"ids": [task_id_1]})
    delete_data = _unwrap(delete_res)
    assert task_id_1 in delete_data["deleted"]
    assert delete_data["count"] == 1

    # Verify first task is deleted
    read_res = mcp_client.call_tool("read_tasks", {"ids": [task_id_1]})
    read_data = _unwrap(read_res)
    assert len(read_data["tasks"]) == 0

    # Verify second task still exists
    read_res2 = mcp_client.call_tool("read_tasks", {"ids": [task_id_2]})
    read_data2 = _unwrap(read_res2)
    assert len(read_data2["tasks"]) == 1
    assert read_data2["tasks"][0]["id"] == task_id_2

    # Delete second task
    delete_res2 = mcp_client.call_tool("delete_tasks", {"ids": [task_id_2]})
    delete_data2 = _unwrap(delete_res2)
    assert task_id_2 in delete_data2["deleted"]

    # Try to delete non-existent task
    delete_res3 = mcp_client.call_tool("delete_tasks", {"ids": [99999]})
    delete_data3 = _unwrap(delete_res3)
    assert delete_data3["count"] == 0
    assert 99999 in delete_data3["not_found"]


def test_list_filters_and_sort(
    mcp_client: SyncMCPClient, unique_task_name: str
) -> None:
    payload = _base_payload(unique_task_name)
    result = mcp_client.call_tool(
        "create_tasks",
        {"tasks": [payload]},
    )
    task_id = _unwrap(result)["created"][0]

    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "status": ["open"],
            "include_description": True,
            "page": 1,
            "page_size": 100,
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["id"] == task_id]
    assert listed, "Task not found in list"
    assert listed[0]["task"]["title"] == payload["title"]


def test_list_multiple_filters(
    mcp_client: SyncMCPClient, unique_task_name: str
) -> None:
    """Test filtering with multiple statuses, priorities, and urgencies."""
    due = (datetime.now(tz=UTC) + timedelta(days=2)).isoformat()

    try:
        # Create task with status="open", priority="high", urgency="high"
        result1 = mcp_client.call_tool(
            "create_tasks",
            {
                "tasks": [
                    {
                        "title": "Multi-filter Task 1",
                        "status": "open",
                        "priority": "high",
                        "urgency": "high",
                        "due_date": due,
                    }
                ],
            },
        )
        task1_id = _unwrap(result1)["created"][0]

        # Create task with status="in-progress", priority="medium", urgency="medium"
        result2 = mcp_client.call_tool(
            "create_tasks",
            {
                "tasks": [
                    {
                        "title": "Multi-filter Task 2",
                        "status": "in-progress",
                        "priority": "medium",
                        "urgency": "medium",
                        "due_date": due,
                    }
                ],
            },
        )
        task2_id = _unwrap(result2)["created"][0]

        # Create task with status="done", priority="low", urgency="low"
        result3 = mcp_client.call_tool(
            "create_tasks",
            {
                "tasks": [
                    {
                        "title": "Multi-filter Task 3",
                        "status": "done",
                        "priority": "low",
                        "urgency": "low",
                        "due_date": due,
                    }
                ],
            },
        )
        task3_id = _unwrap(result3)["created"][0]
    except ToolError as exc:
        if "fast forward" in str(exc).lower():
            pytest.skip("Branch protection or out-of-date ref prevents writes")
        raise

    # Test filtering by multiple statuses
    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "status": ["open", "in-progress"],
            "page_size": 100,
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["id"] in [task1_id, task2_id]]
    assert len(listed) >= 2, "Should find tasks with status 'open' or 'in-progress'"

    # Test filtering by multiple priorities
    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "priority": ["high", "medium"],
            "page_size": 100,
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["id"] in [task1_id, task2_id]]
    assert len(listed) >= 2, "Should find tasks with priority 'high' or 'medium'"

    # Test filtering by multiple urgencies
    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "urgency": ["high", "medium"],
            "page_size": 100,
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["id"] in [task1_id, task2_id]]
    assert len(listed) >= 2, "Should find tasks with urgency 'high' or 'medium'"

    # Test combined filters: multiple statuses AND multiple priorities
    list_res = mcp_client.call_tool(
        "list_tasks",
        {
            "status": ["open", "in-progress"],
            "priority": ["high", "medium"],
            "page_size": 100,
        },
    )
    list_data = _unwrap(list_res)
    listed = [t for t in list_data["tasks"] if t["id"] in [task1_id, task2_id]]
    assert len(listed) >= 2, (
        "Should find tasks matching both status and priority filters"
    )


def test_reminder_tools_happy_path(mcp_client: SyncMCPClient) -> None:
    due_at = (datetime.now(tz=UTC) + timedelta(minutes=2)).isoformat()
    reminder_title = f"e2e-reminder-{uuid.uuid4().hex[:8]}"

    set_res = mcp_client.call_tool(
        "set_reminders",
        {
            "reminders": [
                {
                    "title": reminder_title,
                    "message": "test reminder",
                    "due_at": due_at,
                }
            ]
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

    remove_res = mcp_client.call_tool("remove_reminders", {"ids": [reminder_id]})
    remove_data = _unwrap(remove_res)
    assert remove_data["status"] == "success"

    list_after = mcp_client.call_tool("list_reminders", {})
    list_after_data = _unwrap(list_after)
    if list_after_data.get("status") == "success":
        assert reminder_id not in list_after_data["output"]
