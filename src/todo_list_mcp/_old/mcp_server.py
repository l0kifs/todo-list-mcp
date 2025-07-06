from fastmcp import FastMCP
from .db import init_db, get_or_create_meta, set_worker_pid
from .service import (
    create_task as svc_create_task,
    update_task as svc_update_task,
    delete_task as svc_delete_task,
    list_tasks as svc_list_tasks,
    create_reminder as svc_create_reminder,
    update_reminder as svc_update_reminder,
    delete_reminder as svc_delete_reminder,
    list_reminders as svc_list_reminders,
)
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os
import signal
from .worker import start_worker_as_process

mcp = FastMCP("todo-mcp-server")

class TaskIn(BaseModel):
    description: str
    priority: Optional[str] = "medium"
    status: Optional[str] = "todo"
    deadline: Optional[datetime] = None

class TaskOut(BaseModel):
    id: int
    description: str
    priority: str
    status: str
    deadline: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

class ReminderIn(BaseModel):
    task_id: int
    remind_at: datetime

class ReminderOut(BaseModel):
    id: int
    task_id: int
    remind_at: datetime
    notified: bool
    created_at: datetime

def to_iso_dict(obj):
    """
    Преобразует все поля типа datetime в ISO 8601 строку для соответствия MCP/JSON Schema.
    Работает с pydantic-моделями и dict.
    """
    if hasattr(obj, 'dict'):
        d = obj.dict()
    else:
        d = dict(obj)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif v is None:
            d[k] = None
    return d

@mcp.tool()
def create_task(task: TaskIn) -> dict:
    """
    Создать новую задачу.

    Args:
        task (TaskIn):
            Обязательный параметр. Описание задачи для создания.
            Формат:
                - description (str): Описание задачи. Обязательное поле.
                - priority (str, optional): Приоритет задачи. Допустимые значения: 'low', 'medium', 'high'. По умолчанию 'medium'.
                - status (str, optional): Статус задачи. Допустимые значения: 'todo', 'in_progress', 'done'. По умолчанию 'todo'.
                - deadline (datetime, optional): Дедлайн задачи в формате ISO 8601 (например, '2024-06-13T15:00:00').

    Returns:
        dict: Объект созданной задачи со всеми полями, включая id, даты создания и обновления.

    Raises:
        ValueError: Если обязательные поля не заполнены или некорректный формат данных.

    Пример:
        >>> create_task(TaskIn(description="Позвонить на работу"))
    """
    t = svc_create_task(
        description=task.description,
        priority=task.priority,
        status=task.status,
        deadline=task.deadline
    )
    return to_iso_dict(TaskOut(
        id=t.id,
        description=t.description,
        priority=t.priority,
        status=t.status,
        deadline=t.deadline,
        created_at=t.created_at,
        updated_at=t.updated_at
    ))

@mcp.tool()
def update_task(task_id: int, task: TaskIn) -> Optional[dict]:
    """
    Обновить задачу по id.

    Args:
        task_id (int):
            Обязательный параметр. Идентификатор задачи для обновления.
        task (TaskIn):
            Обязательный параметр. Новые значения для задачи (см. формат TaskIn в create_task).

    Returns:
        Optional[dict]: Обновлённая задача, если найдена и обновлена, иначе None.

    Raises:
        ValueError: Если task_id не существует или некорректные данные.

    Пример:
        >>> update_task(1, TaskIn(description="Обновить описание"))
    """
    t = svc_update_task(
        task_id=task_id,
        description=task.description,
        priority=task.priority,
        status=task.status,
        deadline=task.deadline
    )
    if not t:
        return None
    return to_iso_dict(TaskOut(
        id=t.id,
        description=t.description,
        priority=t.priority,
        status=t.status,
        deadline=t.deadline,
        created_at=t.created_at,
        updated_at=t.updated_at
    ))

@mcp.tool()
def delete_task(task_id: int) -> dict:
    """
    Удалить задачу по id.

    Args:
        task_id (int):
            Обязательный параметр. Идентификатор задачи для удаления.

    Returns:
        dict: Статус удаления. Пример: {"status": "deleted"} или {"status": "not found"}.

    Пример:
        >>> delete_task(1)
    """
    ok = svc_delete_task(task_id)
    return {"status": "deleted" if ok else "not found"}

@mcp.tool()
def list_tasks() -> List[dict]:
    """
    Получить список всех задач.

    Args:
        Нет.

    Returns:
        List[dict]: Список всех задач в системе.

    Пример:
        >>> list_tasks()
    """
    tasks = svc_list_tasks()
    return [to_iso_dict(TaskOut(
        id=t.id,
        description=t.description,
        priority=t.priority,
        status=t.status,
        deadline=t.deadline,
        created_at=t.created_at,
        updated_at=t.updated_at
    )) for t in tasks]

@mcp.tool()
def create_reminder(reminder: ReminderIn) -> dict:
    """
    Создать напоминание (только с привязкой к задаче).

    Args:
        reminder (ReminderIn):
            Обязательный параметр. Описание напоминания.
            Формат:
                - task_id (int): ID задачи, к которой привязывается напоминание. Обязательное поле.
                - remind_at (datetime): Время напоминания в формате ISO 8601 (например, '2024-06-13T15:00:00'). Обязательное поле.

    Returns:
        dict: Объект созданного напоминания со всеми полями, включая id, статус уведомления и дату создания.

    Raises:
        ValueError: Если обязательные поля не заполнены или некорректный формат данных.

    Пример:
        >>> create_reminder(ReminderIn(task_id=1, remind_at="2024-06-13T15:00:00"))
    """
    r = svc_create_reminder(
        task_id=reminder.task_id,
        remind_at=reminder.remind_at
    )
    return to_iso_dict(ReminderOut(
        id=r.id,
        task_id=r.task_id,
        remind_at=r.remind_at,
        notified=r.notified,
        created_at=r.created_at
    ))

@mcp.tool()
def update_reminder(reminder_id: int, remind_at: datetime) -> Optional[dict]:
    """
    Обновить время напоминания по id.

    Args:
        reminder_id (int):
            Обязательный параметр. Идентификатор напоминания для обновления.
        remind_at (datetime):
            Обязательный параметр. Новое время напоминания в формате ISO 8601.

    Returns:
        Optional[dict]: Обновлённое напоминание, если найдено и обновлено, иначе None.

    Raises:
        ValueError: Если reminder_id не существует или некорректный формат времени.

    Пример:
        >>> update_reminder(1, "2024-06-13T16:00:00")
    """
    r = svc_update_reminder(reminder_id, remind_at)
    if not r:
        return None
    return to_iso_dict(ReminderOut(
        id=r.id,
        task_id=r.task_id,
        remind_at=r.remind_at,
        notified=r.notified,
        created_at=r.created_at
    ))

@mcp.tool()
def delete_reminder(reminder_id: int) -> dict:
    """
    Удалить напоминание по id.

    Args:
        reminder_id (int):
            Обязательный параметр. Идентификатор напоминания для удаления.

    Returns:
        dict: Статус удаления. Пример: {"status": "deleted"} или {"status": "not found"}.

    Пример:
        >>> delete_reminder(1)
    """
    ok = svc_delete_reminder(reminder_id)
    return {"status": "deleted" if ok else "not found"}

@mcp.tool()
def list_reminders(task_id: Optional[int] = None) -> List[dict]:
    """
    Получить список напоминаний (по задаче или все).

    Args:
        task_id (int, optional):
            Необязательный параметр. Если указан, возвращаются только напоминания для данной задачи. Если не указан — возвращаются все напоминания.

    Returns:
        List[dict]: Список напоминаний.

    Пример:
        >>> list_reminders()
        >>> list_reminders(task_id=1)
    """
    reminders = svc_list_reminders(task_id=task_id)
    return [to_iso_dict(ReminderOut(
        id=r.id,
        task_id=r.task_id,
        remind_at=r.remind_at,
        notified=r.notified,
        created_at=r.created_at
    )) for r in reminders]

if __name__ == "__main__":
    init_db()
    # Singleton worker process logic
    meta = get_or_create_meta()
    pid = meta.worker_pid
    worker_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            worker_alive = True
        except OSError:
            worker_alive = False
    if not worker_alive:
        new_pid = start_worker_as_process()
        set_worker_pid(new_pid)
    mcp.run(transport="stdio") 