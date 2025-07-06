"""
Todo клиент для управления задачами и напоминаниями.

Основной класс TodoClient предоставляет программный интерфейс для работы
с задачами без CLI зависимостей.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, List
from enum import Enum
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, 
    ForeignKey, Text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from pydantic import BaseModel, Field, field_validator
from loguru import logger


# Enums для типов
class TaskRecurrenceType(str, Enum):
    NONE = "none"
    DAILY = "daily"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReminderUnit(str, Enum):
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"


# SQLAlchemy модели
Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default=TaskStatus.PENDING.value)
    
    # Временные поля
    due_date = Column(DateTime, nullable=True)
    
    # Повторение
    recurrence_type = Column(String(20), default=TaskRecurrenceType.NONE.value)
    recurrence_interval = Column(Integer, default=1)
    bind_to_weekend = Column(Boolean, default=False)
    recurrence_start_date = Column(DateTime, nullable=True)
    
    # Метаданные
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
    
    # Связи
    reminders = relationship("Reminder", back_populates="task", cascade="all, delete-orphan")


class Reminder(Base):
    __tablename__ = "reminders"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    
    # Настройки напоминания
    offset_value = Column(Integer, nullable=False)  # количество единиц
    offset_unit = Column(String(10), nullable=False)  # minutes, hours, days
    message = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Метаданные
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    
    # Связи
    task = relationship("Task", back_populates="reminders")


# Pydantic схемы для валидации
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    recurrence_type: TaskRecurrenceType = TaskRecurrenceType.NONE
    recurrence_interval: int = Field(default=1, ge=1)
    bind_to_weekend: bool = False
    recurrence_start_date: Optional[datetime] = None
    
    @field_validator('due_date')
    @classmethod
    def validate_due_date_iso(cls, v):
        if v is not None and not isinstance(v, datetime):
            raise ValueError('due_date должен быть datetime')
        return v


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    due_date: Optional[datetime] = None
    recurrence_type: Optional[TaskRecurrenceType] = None
    recurrence_interval: Optional[int] = Field(None, ge=1)
    bind_to_weekend: Optional[bool] = None
    recurrence_start_date: Optional[datetime] = None


class ReminderCreate(BaseModel):
    task_id: int
    offset_value: int = Field(..., gt=0)
    offset_unit: ReminderUnit
    message: Optional[str] = None


class ReminderUpdate(BaseModel):
    offset_value: Optional[int] = Field(None, gt=0)
    offset_unit: Optional[ReminderUnit] = None
    message: Optional[str] = None
    is_active: Optional[bool] = None


# Сервисы для работы с задачами
class TaskService:
    def __init__(self, db: Session):
        self.db = db
    
    def create_task(self, task_data: TaskCreate) -> Task:
        """Создать новую задачу."""
        due_date = task_data.due_date
        # Логика привязки к выходному
        if task_data.bind_to_weekend and due_date:
            weekday = due_date.weekday()
            if weekday < 5:
                days_until_saturday = 5 - weekday
                days_until_sunday = 6 - weekday
                if days_until_saturday < days_until_sunday:
                    due_date = due_date + timedelta(days=days_until_saturday)
                else:
                    due_date = due_date + timedelta(days=days_until_sunday)
        task = Task(
            title=task_data.title,
            description=task_data.description,
            due_date=due_date,
            recurrence_type=task_data.recurrence_type.value,
            recurrence_interval=task_data.recurrence_interval,
            bind_to_weekend=task_data.bind_to_weekend,
            recurrence_start_date=task_data.recurrence_start_date
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        logger.info(f"Создана задача: {task.title} (ID: {task.id})")
        return task
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """Получить задачу по ID."""
        return self.db.query(Task).filter(Task.id == task_id).first()
    
    def get_all_tasks(self, status_filter: Optional[TaskStatus] = None) -> List[Task]:
        """Получить все задачи с опциональной фильтрацией по статусу."""
        query = self.db.query(Task)
        if status_filter:
            query = query.filter(Task.status == status_filter.value)
        return query.order_by(Task.due_date.asc(), Task.created_at.desc()).all()
    
    def update_task(self, task_id: int, task_data: TaskUpdate) -> Optional[Task]:
        """Обновить задачу."""
        task = self.get_task(task_id)
        if not task:
            return None
        
        # Обновляем только переданные поля
        update_data = task_data.model_dump(exclude_unset=True)
        
        # Обрабатываем enum значения
        if 'status' in update_data:
            update_data['status'] = update_data['status'].value
        if 'recurrence_type' in update_data:
            update_data['recurrence_type'] = update_data['recurrence_type'].value
        
        for field, value in update_data.items():
            setattr(task, field, value)
        
        task.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        
        logger.info(f"Обновлена задача ID: {task_id}")
        return task
    
    def delete_task(self, task_id: int) -> bool:
        """Удалить задачу."""
        task = self.get_task(task_id)
        if not task:
            return False
        
        self.db.delete(task)
        self.db.commit()
        
        logger.info(f"Удалена задача ID: {task_id}")
        return True
    
    def get_tasks_with_reminders_due(self, check_datetime: datetime) -> List[Task]:
        """Получить задачи с напоминаниями, которые должны сработать."""
        tasks_with_reminders = []
        
        tasks = self.db.query(Task).join(Reminder).filter(
            Reminder.is_active == True
        ).all()
        
        for task in tasks:
            for reminder in task.reminders:
                if not reminder.is_active:
                    continue
                
                reminder_time = self._calculate_reminder_time(task, reminder)
                if reminder_time and abs((reminder_time - check_datetime).total_seconds()) < 60:
                    tasks_with_reminders.append(task)
                    break
        
        return tasks_with_reminders
    
    def _calculate_reminder_time(self, task: Task, reminder: Reminder) -> Optional[datetime]:
        """Вычислить время напоминания для задачи."""
        if not task.due_date:
            return None
        
        task_datetime = task.due_date
        if reminder.offset_unit == ReminderUnit.MINUTES.value:
            offset = timedelta(minutes=reminder.offset_value)
        elif reminder.offset_unit == ReminderUnit.HOURS.value:
            offset = timedelta(hours=reminder.offset_value)
        elif reminder.offset_unit == ReminderUnit.DAYS.value:
            offset = timedelta(days=reminder.offset_value)
        else:
            return None
        
        return task_datetime - offset


class ReminderService:
    def __init__(self, db: Session):
        self.db = db
    
    def create_reminder(self, reminder_data: ReminderCreate) -> Optional[Reminder]:
        """Создать напоминание для задачи."""
        # Проверяем, существует ли задача
        task = self.db.query(Task).filter(Task.id == reminder_data.task_id).first()
        if not task:
            return None
        
        reminder = Reminder(
            task_id=reminder_data.task_id,
            offset_value=reminder_data.offset_value,
            offset_unit=reminder_data.offset_unit.value,
            message=reminder_data.message
        )
        
        self.db.add(reminder)
        self.db.commit()
        self.db.refresh(reminder)
        
        logger.info(f"Создано напоминание для задачи ID: {reminder_data.task_id}")
        return reminder
    
    def get_reminder(self, reminder_id: int) -> Optional[Reminder]:
        """Получить напоминание по ID."""
        return self.db.query(Reminder).filter(Reminder.id == reminder_id).first()
    
    def get_task_reminders(self, task_id: int) -> List[Reminder]:
        """Получить все напоминания для задачи."""
        return self.db.query(Reminder).filter(Reminder.task_id == task_id).all()
    
    def update_reminder(self, reminder_id: int, reminder_data: ReminderUpdate) -> Optional[Reminder]:
        """Обновить напоминание."""
        reminder = self.get_reminder(reminder_id)
        if not reminder:
            return None
        
        update_data = reminder_data.dict(exclude_unset=True)
        
        # Обрабатываем enum значения
        if 'offset_unit' in update_data:
            update_data['offset_unit'] = update_data['offset_unit'].value
        
        for field, value in update_data.items():
            setattr(reminder, field, value)
        
        self.db.commit()
        self.db.refresh(reminder)
        
        logger.info(f"Обновлено напоминание ID: {reminder_id}")
        return reminder
    
    def delete_reminder(self, reminder_id: int) -> bool:
        """Удалить напоминание."""
        reminder = self.get_reminder(reminder_id)
        if not reminder:
            return False
        
        self.db.delete(reminder)
        self.db.commit()
        
        logger.info(f"Удалено напоминание ID: {reminder_id}")
        return True


# Основной клиент по аналогии с LLMClient
class TodoClient:
    def __init__(self, db_path: Optional[str] = None):
        """
        Инициализирует Todo клиент.
        
        Args:
            db_path: Путь к файлу базы данных SQLite. 
                    По умолчанию: todo_tasks.db в текущей директории
        """
        if db_path is None:
            db_path = "todo_tasks.db"
        
        self.db_path = Path(db_path)
        
        logger.info(f"Initializing TodoClient with database: {self.db_path}")
        
        # Настройка SQLAlchemy
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
        # Инициализация базы данных
        self.init_db()
        
        logger.info("TodoClient initialized successfully")
    
    def init_db(self):
        """Инициализировать базу данных."""
        Base.metadata.create_all(bind=self.engine)
        logger.info("База данных инициализирована")
    
    def _get_db(self) -> Session:
        """Получить сессию базы данных."""
        return self.SessionLocal()
    
    # API для работы с задачами
    def create_task(self, title: str, description: Optional[str] = None, 
                   due_date: Optional[datetime] = None,
                   recurrence_type: TaskRecurrenceType = TaskRecurrenceType.NONE,
                   recurrence_interval: int = 1,
                   bind_to_weekend: bool = False,
                   recurrence_start_date: Optional[datetime] = None) -> Task:
        """
        Создать новую задачу.
        Args:
            title: Название задачи
            description: Описание задачи
            due_date: Дата и время выполнения (datetime, ISO 8601)
            recurrence_type: Тип повторения (только DAILY или NONE)
            recurrence_interval: Интервал повторения (каждые N дней)
            bind_to_weekend: Привязка к ближайшему выходному
            recurrence_start_date: Дата начала повторения (datetime)
        Returns:
            Созданная задача
        """
        task_data = TaskCreate(
            title=title,
            description=description,
            due_date=due_date,
            recurrence_type=recurrence_type,
            recurrence_interval=recurrence_interval,
            bind_to_weekend=bind_to_weekend,
            recurrence_start_date=recurrence_start_date
        )
        with self._get_db() as db:
            service = TaskService(db)
            return service.create_task(task_data)
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """Получить задачу по ID."""
        with self._get_db() as db:
            service = TaskService(db)
            return service.get_task(task_id)
    
    def get_all_tasks(self, status_filter: Optional[TaskStatus] = None) -> List[Task]:
        """Получить все задачи с опциональной фильтрацией по статусу."""
        with self._get_db() as db:
            service = TaskService(db)
            return service.get_all_tasks(status_filter)
    
    def update_task(self, task_id: int, **kwargs) -> Optional[Task]:
        """
        Обновить задачу.
        
        Args:
            task_id: ID задачи
            **kwargs: Поля для обновления
        
        Returns:
            Обновленная задача или None если не найдена
        """
        task_data = TaskUpdate(**kwargs)
        
        with self._get_db() as db:
            service = TaskService(db)
            return service.update_task(task_id, task_data)
    
    def delete_task(self, task_id: int) -> bool:
        """Удалить задачу."""
        with self._get_db() as db:
            service = TaskService(db)
            return service.delete_task(task_id)
    
    def mark_task_completed(self, task_id: int) -> Optional[Task]:
        """Отметить задачу как завершенную."""
        return self.update_task(task_id, status=TaskStatus.COMPLETED)
    
    def mark_task_in_progress(self, task_id: int) -> Optional[Task]:
        """Отметить задачу как в процессе."""
        return self.update_task(task_id, status=TaskStatus.IN_PROGRESS)
    
    # API для работы с напоминаниями
    def create_reminder(self, task_id: int, offset_value: int, 
                       offset_unit: ReminderUnit, message: Optional[str] = None) -> Optional[Reminder]:
        """
        Создать напоминание для задачи.
        
        Args:
            task_id: ID задачи
            offset_value: Количество единиц времени до задачи
            offset_unit: Единица времени (minutes, hours, days)
            message: Сообщение напоминания
        
        Returns:
            Созданное напоминание или None если задача не найдена
        """
        reminder_data = ReminderCreate(
            task_id=task_id,
            offset_value=offset_value,
            offset_unit=offset_unit,
            message=message
        )
        
        with self._get_db() as db:
            service = ReminderService(db)
            return service.create_reminder(reminder_data)
    
    def get_task_reminders(self, task_id: int) -> List[Reminder]:
        """Получить все напоминания для задачи."""
        with self._get_db() as db:
            service = ReminderService(db)
            return service.get_task_reminders(task_id)
    
    def update_reminder(self, reminder_id: int, **kwargs) -> Optional[Reminder]:
        """Обновить напоминание."""
        reminder_data = ReminderUpdate(**kwargs)
        
        with self._get_db() as db:
            service = ReminderService(db)
            return service.update_reminder(reminder_id, reminder_data)
    
    def delete_reminder(self, reminder_id: int) -> bool:
        """Удалить напоминание."""
        with self._get_db() as db:
            service = ReminderService(db)
            return service.delete_reminder(reminder_id)
    
    def check_reminders(self, check_datetime: Optional[datetime] = None) -> List[Task]:
        """
        Проверить задачи с напоминаниями, которые должны сработать.
        
        Args:
            check_datetime: Время для проверки. По умолчанию - текущее время
        
        Returns:
            Список задач с активными напоминаниями
        """
        if check_datetime is None:
            check_datetime = datetime.now()
        
        with self._get_db() as db:
            service = TaskService(db)
            return service.get_tasks_with_reminders_due(check_datetime)


# Пример использования
if __name__ == "__main__":
    # Создаем клиент
    client = TodoClient()
    
    # Создаем простую задачу
    task = client.create_task(
        title="Купить продукты",
        description="Хлеб, молоко, яйца"
    )
    print(f"Создана задача: {task.title} (ID: {task.id})")
    
    # Создаем задачу с датой
    task_with_date = client.create_task(
        title="Встреча с клиентом",
        due_date=datetime(2025, 7, 10, 14, 30)
    )
    print(f"Создана задача с датой: {task_with_date.title} (ID: {task_with_date.id})")
    
    # Создаем напоминание
    reminder = client.create_reminder(
        task_id=task_with_date.id,
        offset_value=30,
        offset_unit=ReminderUnit.MINUTES,
        message="Подготовить документы для встречи"
    )
    print(f"Создано напоминание (ID: {reminder.id})")
    
    # Получаем все задачи
    tasks = client.get_all_tasks()
    print(f"Всего задач: {len(tasks)}")
    
    # Отмечаем задачу как завершенную
    completed_task = client.mark_task_completed(task.id)
    print(f"Задача {completed_task.id} отмечена как завершенная")
    
    # Проверяем напоминания
    reminders_due = client.check_reminders()
    print(f"Активных напоминаний: {len(reminders_due)}")