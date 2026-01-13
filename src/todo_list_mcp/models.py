"""SQLAlchemy ORM models for todo-list MCP.

This module defines the database schema for tasks and reminders using SQLAlchemy's
declarative mapping. Models map directly to the previous YAML structure for
backward compatibility.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy import JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class Task(Base):
    """Task model representing a todo item.

    Stores all task metadata including status, priority, urgency, time estimates,
    due dates, tags, and assignee information. Fields map directly to the previous
    YAML structure for compatibility.
    """

    __tablename__ = "tasks"

    # Primary key (auto-generated)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Core fields
    title: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Status and priority fields (indexed for filtering)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", index=True
    )
    priority: Mapped[str] = mapped_column(
        String, nullable=False, default="medium", index=True
    )
    urgency: Mapped[str] = mapped_column(
        String, nullable=False, default="medium", index=True
    )

    # Time tracking
    time_estimate: Mapped[Optional[float]] = mapped_column(nullable=True)
    due_date: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    # Tags stored as JSON array for flexibility
    tags: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)

    # Assignment
    assignee: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    # Timestamps (auto-managed)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda: datetime.now(tz=UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda: datetime.now(tz=UTC).isoformat()
    )

    def __repr__(self) -> str:
        return f"Task(id={self.id}, title={self.title!r}, status={self.status!r})"

    def to_dict(self) -> dict:
        """Convert task to dictionary matching YAML structure."""
        return {
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "urgency": self.urgency,
            "time_estimate": self.time_estimate,
            "due_date": self.due_date,
            "tags": self.tags or [],
            "assignee": self.assignee,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Reminder(Base):
    """Reminder model for scheduled notifications.

    Stores reminders with optional task associations. Reminders can be linked
    to tasks by task_id for integrated workflow.
    """

    __tablename__ = "reminders"

    # Primary key
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Core fields
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    due_at: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Optional task association
    task_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)

    # Status tracking
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", index=True
    )

    # Timestamps
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda: datetime.now(tz=UTC).isoformat()
    )

    def __repr__(self) -> str:
        return f"Reminder(id={self.id}, title={self.title!r}, due_at={self.due_at!r})"

    def to_dict(self) -> dict:
        """Convert reminder to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "due_at": self.due_at,
            "task_id": self.task_id,
            "status": self.status,
            "created_at": self.created_at,
        }
