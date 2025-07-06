from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    description = Column(String(1024), nullable=False)
    priority = Column(String(16), default='medium')
    status = Column(String(32), default='todo')
    deadline = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    reminders = relationship('Reminder', back_populates='task', cascade="all, delete-orphan")

class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    remind_at = Column(DateTime, nullable=False)
    notified = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    task = relationship('Task', back_populates='reminders')

class WorkerLock(Base):
    __tablename__ = 'worker_lock'
    id = Column(Integer, primary_key=True)
    locked = Column(Boolean, default=False, nullable=False)

class MCPMeta(Base):
    __tablename__ = 'mcp_meta'
    id = Column(Integer, primary_key=True)
    worker_pid = Column(Integer, nullable=True) 