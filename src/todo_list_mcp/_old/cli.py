import typer
from .db import init_db, SessionLocal, get_or_create_meta, set_worker_pid
from .worker import start_worker_as_process
from .models import WorkerLock
from loguru import logger
import os
import signal

app = typer.Typer()

@app.command()
def initdb():
    """Инициализация базы данных."""
    init_db()
    typer.echo("База данных инициализирована.")

@app.command()
def start_worker():
    """Запустить singleton-воркер напоминаний (отдельный процесс)."""
    init_db()
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
        typer.echo(f"Воркер запущен как процесс с PID {new_pid}")
    else:
        typer.echo(f"Воркер уже запущен с PID {pid}")

@app.command()
def stop_worker():
    """Остановить singleton-воркер по PID."""
    meta = get_or_create_meta()
    pid = meta.worker_pid
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            set_worker_pid(None)
            typer.echo(f"Воркер с PID {pid} остановлен и PID очищен.")
        except OSError:
            set_worker_pid(None)
            typer.echo(f"Процесс с PID {pid} не найден. PID очищен.")
    else:
        typer.echo("PID воркера не найден в базе.") 