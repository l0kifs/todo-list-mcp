# Reminder Daemon

A standalone, persistent reminder service with CLI interface and desktop notifications.

## Overview

The reminder daemon is a simple, reliable reminder service that:
- Stores reminders persistently in JSON format (`~/.reminder_daemon/reminders.json`)
- Runs as a background daemon until terminated or system shutdown
- Delivers desktop notifications with optional sound alerts
- Provides a clean CLI interface using Typer

## Installation

The reminder daemon is installed as part of the `todo-list-mcp` package:

```bash
uv pip install -e .
```

This adds the `reminder-daemon` command to your PATH.

## Usage

### Starting the Daemon

Run the daemon in the foreground (recommended for systemd or supervisor):

```bash
reminder-daemon daemon
```

Or run in the background:

```bash
nohup reminder-daemon daemon > /dev/null 2>&1 &
```

### Adding Reminders

Add a single reminder:

```bash
reminder-daemon add "Team Meeting" "Quarterly planning" "2026-01-15T10:00:00Z"
```

Add multiple reminders:

```bash
reminder-daemon add "Call Client" "Follow up on proposal" "2026-01-15T14:00:00Z"
reminder-daemon add "Code Review" "Review PR #123" "2026-01-15T16:00:00Z"
```

### Listing Reminders

View all pending reminders:

```bash
reminder-daemon list
```

This shows a table with:
- Reminder ID
- Title
- Message
- Due time
- Status (PENDING or DUE)

### Removing Reminders

Remove specific reminders by ID:

```bash
reminder-daemon remove abc123 def456
```

Remove all reminders:

```bash
reminder-daemon remove --all
```

## Integration with MCP Server

The MCP server provides three tools for managing reminders:

### 1. `set_reminders` - Add reminders

```json
{
  "reminders": [
    {
      "title": "Meeting",
      "message": "Team standup",
      "due_at": "2026-01-15T10:00:00Z"
    }
  ]
}
```

### 2. `list_reminders` - View all reminders

```json
{}
```

### 3. `remove_reminders` - Remove reminders

Remove specific reminders:
```json
{
  "ids": ["abc123", "def456"]
}
```

Remove all reminders:
```json
{
  "all": true
}
```

## Architecture

### Components

1. **ReminderStore**: Manages JSON persistence with thread-safe file operations
2. **ReminderDaemon**: Background thread that checks reminders every second
3. **CLI Interface**: Typer-based command-line interface for management

### Data Storage

Reminders are stored in `~/.reminder_daemon/reminders.json`:

```json
[
  {
    "id": "abc123",
    "title": "Meeting",
    "message": "Team standup",
    "due_at": "2026-01-15T10:00:00Z",
    "created_at": "2026-01-11T08:00:00Z"
  }
]
```

### Notification Delivery

When a reminder is due:
1. Desktop notification is shown via `ReminderClient`
2. Optional sound alert is played via `SoundClient`
3. Reminder is automatically removed from storage

## Design Principles

- **Simple**: Single-file implementation, easy to understand
- **Reliable**: Thread-safe operations, error handling
- **Persistent**: Survives restarts, stores state in JSON
- **Independent**: No dependencies on MCP server internals

## Differences from Original `reminder_sidecar.py`

| Feature       | Original Sidecar              | New Daemon            |
| ------------- | ----------------------------- | --------------------- |
| Integration   | Tightly coupled to MCP server | Standalone, CLI-based |
| Storage       | In-memory only                | JSON file persistence |
| Interface     | Internal Python API           | Typer CLI             |
| Lifecycle     | Daemon thread                 | Independent process   |
| Communication | Direct Python calls           | CLI subprocess calls  |

## Systemd Service Example

Create `/etc/systemd/system/reminder-daemon.service`:

```ini
[Unit]
Description=Reminder Daemon
After=network.target

[Service]
Type=simple
User=%i
ExecStart=/usr/local/bin/reminder-daemon daemon
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable reminder-daemon
sudo systemctl start reminder-daemon
```

## Troubleshooting

### Daemon not starting

Check if `ReminderClient` and `SoundClient` dependencies are available:

```bash
python -c "from todo_list_mcp.reminder_client import ReminderClient; ReminderClient()"
```

### Reminders not being delivered

1. Verify daemon is running: `ps aux | grep reminder-daemon`
2. Check reminder times: `reminder-daemon list`
3. Ensure times are in UTC and ISO 8601 format

### JSON file corruption

Manually inspect or reset:

```bash
cat ~/.reminder_daemon/reminders.json
# If corrupted:
echo "[]" > ~/.reminder_daemon/reminders.json
```
