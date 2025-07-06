from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Enum, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session
from enum import Enum as PyEnum
from datetime import datetime
from loguru import logger
from threading import Thread
from queue import Queue
from typing import Optional

import threading

Base = declarative_base()

class Priority(PyEnum):
    HIGH = "высокий"
    MEDIUM = "средний"
    LOW = "низкий"

class State(PyEnum):
    PENDING = "не выполнена"
    DONE = "выполнена"

def now_iso():
    return datetime.utcnow().isoformat()

# --- Модели ---
class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(Enum(Priority), nullable=False)
    due_date = Column(DateTime, nullable=True)
    state = Column(Enum(State), default=State.PENDING)
    created_at = Column(String, default=now_iso)
    updated_at = Column(String, default=now_iso)

    reminders = relationship("Reminder", back_populates="task", cascade="all, delete-orphan")

class Reminder(Base):
    __tablename__ = 'reminders'

    id = Column(Integer, primary_key=True)
    remind_at = Column(DateTime, nullable=False)
    created_at = Column(String, default=now_iso)
    updated_at = Column(String, default=now_iso)

    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    task = relationship("Task", back_populates="reminders")

# --- Менеджер задач ---
class TaskManager:
    """
    Менеджер задач с поддержкой многопоточности и очереди операций.

    Позволяет управлять задачами и напоминаниями с помощью SQLAlchemy ORM.

    Особенности:
    - Поддержка CRUD-операций для задач и напоминаний.
    - Асинхронная обработка операций через внутреннюю очередь и отдельный поток.
    - Поддержка паттерна Singleton (опционально через параметр singleton=True).
    - Возможность указать URL базы данных при создании объекта.

    Аргументы конструктора:
        db_url (str, optional): URL базы данных SQLAlchemy (например, 'sqlite:///tasks.db').
            Если не указан, используется SQLite-файл по умолчанию ('sqlite:///tasks.db').
        singleton (bool, optional): Если True — используется singleton-экземпляр менеджера.

    Пример использования:
        manager = TaskManager(db_url="sqlite:///my_tasks.db")
        manager = TaskManager(singleton=True)  # singleton с БД по умолчанию
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        db_url = kwargs.get("db_url", None)
        if kwargs.get("singleton", False):
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_db(db_url)
                    cls._instance._start_worker()
                return cls._instance
        else:
            obj = super().__new__(cls)
            obj._init_db(db_url)
            obj._start_worker()
            return obj

    def _init_db(self, db_url=None):
        if db_url is None:
            db_url = "sqlite:///tasks.db"
        self.engine = create_engine(db_url, echo=False, connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {})
        Base.metadata.create_all(self.engine)
        self.Session = scoped_session(sessionmaker(bind=self.engine))
        self.queue = Queue()
        logger.info(f"TaskManager initialized with DB: {db_url}")

    def _start_worker(self):
        self.worker = Thread(target=self._process_queue, daemon=True)
        self.worker.start()
        logger.info("TaskManager worker thread started.")

    def _process_queue(self):
        while True:
            func, args, kwargs, result = self.queue.get()
            try:
                logger.debug(f"Executing {func.__name__} with {args} {kwargs}")
                res = func(*args, **kwargs)
                result.put(res)
            except Exception as e:
                logger.exception("Error during queue processing")
                result.put(e)

    def _enqueue(self, func, *args, **kwargs):
        result = Queue()
        self.queue.put((func, args, kwargs, result))
        output = result.get()
        if isinstance(output, Exception):
            raise output
        return output

    # --- CRUD задачи ---
    def create_task(self, name: str, description: str, priority: Priority, due_date: Optional[datetime] = None):
        def _create():
            session = self.Session()
            task = Task(name=name, description=description, priority=priority, due_date=due_date)
            session.add(task)
            session.commit()
            logger.info(f"Task '{name}' created.")
            return task.id
        return self._enqueue(_create)

    def get_task(self, task_id: int) -> Optional[Task]:
        def _get():
            session = self.Session()
            return session.get(Task, task_id)
        return self._enqueue(_get)

    def update_task(self, task_id: int, **fields):
        def _update():
            session = self.Session()
            task = session.get(Task, task_id)
            if not task:
                return None
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.updated_at = now_iso()
            session.commit()
            logger.info(f"Task {task_id} updated.")
            return True
        return self._enqueue(_update)

    def delete_task(self, task_id: int):
        def _delete():
            session = self.Session()
            task = session.get(Task, task_id)
            if task:
                session.delete(task)
                session.commit()
                logger.info(f"Task {task_id} deleted.")
                return True
            return False
        return self._enqueue(_delete)

    # --- CRUD напоминаний ---
    def create_reminder(self, task_id: int, remind_at: datetime):
        def _create():
            session = self.Session()
            task = session.get(Task, task_id)
            if not task:
                raise ValueError("Task does not exist")
            reminder = Reminder(remind_at=remind_at, task=task)
            session.add(reminder)
            session.commit()
            logger.info(f"Reminder for task {task_id} created.")
            return reminder.id
        return self._enqueue(_create)

    def get_reminder(self, reminder_id: int) -> Optional[Reminder]:
        def _get():
            session = self.Session()
            return session.get(Reminder, reminder_id)
        return self._enqueue(_get)

    def update_reminder(self, reminder_id: int, remind_at: datetime):
        def _update():
            session = self.Session()
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None
            reminder.remind_at = remind_at
            reminder.updated_at = now_iso()
            session.commit()
            logger.info(f"Reminder {reminder_id} updated.")
            return True
        return self._enqueue(_update)

    def delete_reminder(self, reminder_id: int):
        def _delete():
            session = self.Session()
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                session.delete(reminder)
                session.commit()
                logger.info(f"Reminder {reminder_id} deleted.")
                return True
            return False
        return self._enqueue(_delete)


# Пример использования
if __name__ == "__main__":
    from datetime import timedelta

    manager = TaskManager(singleton=True)

    task_id = manager.create_task(
        name="Сделать проект",
        description="Не забыть про дедлайн!",
        priority=Priority.HIGH,
        due_date=datetime.utcnow() + timedelta(days=1)
    )

    reminder_id = manager.create_reminder(task_id=task_id, remind_at=datetime.utcnow() + timedelta(hours=2))

    task = manager.get_task(task_id)
    print(task.name, task.due_date)

    manager.update_task(task_id, state=State.DONE)

    # manager.delete_reminder(reminder_id)
    # manager.delete_task(task_id)
