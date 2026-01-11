# Todo List MCP Server

A Model Context Protocol (MCP) server that provides:
- **Todo List Management**: Persistent todo list with GitHub integration
- **Reminder Service**: Desktop notifications with optional sound alerts
- **Cross-platform Sound System**: Sound playback on Windows, macOS, and Linux

## Features

### Default Reminder Sound
The package includes a pleasant chime sound (`reminder_chime.wav`) that plays automatically when reminders are triggered. No configuration needed - it works out of the box!

### Sound Client
Cross-platform sound playback with:
- Automatic fallback to default sound when no source is specified
- Support for WAV files on all platforms
- Additional format support (MP3, AIFF) on macOS
- Loop playback with configurable intervals
- Thread-safe operation

### Reminder Daemon
Persistent reminder service with:
- JSON-based storage in `~/.reminder_daemon/`
- Desktop notifications via `ReminderClient`
- Optional sound alerts via `SoundClient`
- CLI interface for management
