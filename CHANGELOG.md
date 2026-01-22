# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- None yet

### Changed
- None yet

### Fixed
- None yet

## [0.3.0] - 2026-01-22

### Added
- wxPython-based reminder client for improved cross-platform desktop notifications
- macOS-specific threading and UI handling for wxPython client

### Changed
- Replaced Tkinter with wxPython in reminder CLI daemon for better stability
- Improved reminder CLI daemon to run wxPython on main thread on macOS
- Enhanced AI assistant prompt documentation with better task filtering examples and time awareness

### Fixed
- Cross-platform compatibility issues with reminder dialogs

## [0.2.0] - 2026-01-13

### Changed
- **BREAKING**: Migrated from GitHub-based storage to SQLite database for improved performance and simplified setup
- Removed GitHub PAT requirement - no authentication needed anymore
- Replaced `archive_tasks` tool with database-based task management
- Updated `create_tasks`, `read_tasks`, `update_tasks`, and `list_tasks` to use task IDs instead of filenames
- Simplified configuration - only database URL needed (optional, has sensible defaults)

### Added
- SQLAlchemy ORM models for tasks and reminders
- SQLite client for database operations with transaction support
- Database initialization and schema management

### Removed
- GitHub file client and all GitHub integration code
- Dependency on GitHub repository for task storage
- `archive_tasks` tool (replaced with database queries)

## [0.1.4] - 2026-01-12

### Changed
- Enhanced documentation for `list_tasks` function parameters with improved docstrings
- Clarified filtering capabilities and default behavior for status, priority, urgency, tags, assignee, due_before, due_after, and page parameters

## [0.1.3] - 2026-01-11

### Changed
- Updated type annotations in `list_tasks` function for improved clarity

## [0.1.2] - 2026-01-11

### Added
- Enhanced `list_tasks` function with support for multiple filters for status, priority, and urgency
- Expanded test coverage for end-to-end MCP functionality

### Changed
- Improved filtering capabilities in task listing

### Fixed
- Updated README to specify latest version for todo-list-mcp

## [0.1.1] - 2026-01-11

### Changed
- Streamlined request handling in MCP server for better code organization
- Enhanced function signatures with type annotations for request bodies
- Updated README with correct envFile path configuration
- Updated publishing guidelines to include change analysis step

## [0.1.0] - 2026-01-11

### Added
- Todo List Management with GitHub integration
- Reminder service with desktop notifications
- Cross-platform sound system support
- MCP server implementation with FastMCP
- CLI interface for reminder management
- Background daemon for reminder notifications
- Support for Windows, macOS, and Linux

[Unreleased]: https://github.com/l0kifs/todo-list-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.2.0
[0.1.4]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.1.4
[0.1.3]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/l0kifs/todo-list-mcp/releases/tag/v0.1.0
