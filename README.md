<div align="center">

![todo-list-mcp](https://socialify.git.ci/l0kifs/todo-list-mcp/image?description=1&font=Inter&language=1&name=1&owner=1&pattern=Signal&theme=Light)

# Todo List MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

</div>

A Model Context Protocol (MCP) server that provides:
- **Todo List Management**: Persistent todo list with GitHub integration
- **Reminder Service**: Desktop notifications with optional sound alerts
- **Cross-platform Sound System**: Sound playback on Windows, macOS, and Linux

## Quick Start

### Prerequisites
- [UV](https://docs.astral.sh/uv/) installed
- A GitHub Personal Access Token (PAT) with `repo` scope

### Configuration
Create a `.env` file in ``~/.todo-list-mcp/`` with the following content:
```env
TODO_LIST_MCP__GITHUB_FILE_CLIENT_SETTINGS__OWNER=your_username
TODO_LIST_MCP__GITHUB_FILE_CLIENT_SETTINGS__REPO=your_repo_name
TODO_LIST_MCP__GITHUB_FILE_CLIENT_SETTINGS__TOKEN=ghp_your_token_here
```

### VSCode IDE Setup
Enter the following details in your `mcp.json` configuration file:

```json
"todo-list-mcp": {
    "type": "stdio",
    "command": "uvx",
    "args": [
        "todo-list-mcp@latest"
    ],
    "envFile": "path/to/user/home/.todo-list-mcp/.env"
},
```

## Features

### Todo List Management (MCP)
- **GitHub Integration**: Store tasks as YAML files in a GitHub repository `tasks/` directory
- **Flexible Attributes**: Track title, description, status, priority, urgency, time estimates, due dates, tags, and assignees
- **Smart Filtering**: Query tasks by status, priority, tags, assignee, or due date
- **Lifecycle Management**: Create, read, update, and archive tasks directly via MCP tools

### Reminder System
- **Cross-Platform**: Native visual dialogs for Windows, macOS, and Linux
- **Background Service**: Reliable daemon process ensures timely notifications
- **Persistence**: Local JSON storage in `~/.todo-list-mcp/reminder_daemon/` keeps reminders safe

### Sound System
- **Universal Playback**: Audio alerts on all supported operating systems
- **Built-in Assets**: Includes a chime sound out of the box
- **Advanced Audio**: Support for custom WAV files and loop playback with configurable intervals
