<div align="center">

![todo-list-mcp](https://socialify.git.ci/l0kifs/todo-list-mcp/image?description=1&font=Inter&language=1&name=1&owner=1&pattern=Signal&theme=Light)

# Todo List MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

</div>

A Model Context Protocol (MCP) server that provides:
- **Todo List Management**: Persistent todo list with SQLite database storage
- **Reminder Service**: Desktop notifications with optional sound alerts
- **Cross-platform Sound System**: Sound playback on Windows, macOS, and Linux

## Quick Start

### Prerequisites
- [UV](https://docs.astral.sh/uv/) installed
- Python 3.11 or higher

### Configuration
The application stores data in `~/.todo-list-mcp/` directory:
- `todo_list.db` - SQLite database for tasks
- `reminder_daemon/` - Reminder daemon data

No additional configuration is required unless you want to customize the database location.
You can optionally create a `.env` file in `~/.todo-list-mcp/` with:
```env
TODO_LIST_MCP__DATABASE_URL=sqlite:///path/to/custom/location.db
```

### VSCode IDE Setup
Enter the following details in your `mcp.json` configuration file:

```json
"todo-list-mcp": {
    "type": "stdio",
    "command": "uvx",
    "args": [
        "todo-list-mcp@latest"
    ]
}
```

## Features

### Todo List Management (MCP)
- **SQLite Storage**: Store tasks in a local SQLite database for fast, reliable access
- **Flexible Attributes**: Track title, description, status, priority, urgency, time estimates, due dates, tags, and assignees
- **Smart Filtering**: Query tasks by status, priority, tags, assignee, or due date
- **Lifecycle Management**: Create, read, update, and archive tasks directly via MCP tools
- **Archiving**: Archive completed tasks while preserving all data for future reference

### Reminder System
- **Cross-Platform**: Native visual dialogs for Windows, macOS, and Linux
- **Background Service**: Reliable daemon process ensures timely notifications
- **Persistence**: Local JSON storage in `~/.todo-list-mcp/reminder_daemon/` keeps reminders safe

### Sound System
- **Universal Playback**: Audio alerts on all supported operating systems
- **Built-in Assets**: Includes a chime sound out of the box
- **Advanced Audio**: Support for custom WAV files and loop playback with configurable intervals
