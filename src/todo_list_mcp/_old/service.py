from .db import SessionLocal
from .models import Task, Reminder
from datetime import datetime
from typing import Optional, List

# TASKS

def create_task(description: str, priority: str = "medium", status: str = "todo", deadline: Optional[datetime] = None) -> Task:
    with SessionLocal() as db:
        t = Task(
            description=description,
            priority=priority,
            status=status,
            deadline=deadline
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

def update_task(task_id: int, description: str, priority: str, status: str, deadline: Optional[datetime]) -> Optional[Task]:
    with SessionLocal() as db:
        t = db.query(Task).filter_by(id=task_id).first()
        if not t:
            return None
        t.description = description
        t.priority = priority
        t.status = status
        t.deadline = deadline
        db.commit()
        db.refresh(t)
        return t

def delete_task(task_id: int) -> bool:
    with SessionLocal() as db:
        t = db.query(Task).filter_by(id=task_id).first()
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True

def list_tasks() -> List[Task]:
    with SessionLocal() as db:
        return db.query(Task).all()

# REMINDERS

def create_reminder(task_id: int, remind_at: datetime) -> Reminder:
    with SessionLocal() as db:
        t = db.query(Task).filter_by(id=task_id).first()
        if not t:
            raise ValueError("Task not found")
        r = Reminder(
            task_id=task_id,
            remind_at=remind_at
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        return r

def update_reminder(reminder_id: int, remind_at: datetime) -> Optional[Reminder]:
    with SessionLocal() as db:
        r = db.query(Reminder).filter_by(id=reminder_id).first()
        if not r:
            return None
        r.remind_at = remind_at
        db.commit()
        db.refresh(r)
        return r

def delete_reminder(reminder_id: int) -> bool:
    with SessionLocal() as db:
        r = db.query(Reminder).filter_by(id=reminder_id).first()
        if not r:
            return False
        db.delete(r)
        db.commit()
        return True

def list_reminders(task_id: Optional[int] = None) -> List[Reminder]:
    with SessionLocal() as db:
        q = db.query(Reminder)
        if task_id:
            q = q.filter_by(task_id=task_id)
        return q.all() 