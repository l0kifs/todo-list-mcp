import os
import time
from datetime import datetime
from sqlalchemy.orm import Session
from .db import SessionLocal, set_worker_pid, get_or_create_meta
from .models import Reminder, Task
from loguru import logger
from plyer import notification
import multiprocessing
import threading
import sys
try:
    import tkinter as tk
except ImportError:
    tk = None

class Worker:
    def __init__(self, poll_interval=10):
        self.poll_interval = poll_interval
        self.running = False

    def show_custom_notification(self, title: str, message: str, reminder=None, db: Session = None):
        """
        Показывает кастомное окно через PyQt6 с beep, повторяющимся каждые 5 минут,
        пока окно не будет закрыто. Если PyQt6 не доступен или нет GUI — логирует ошибку.
        Если reminder и db переданы, выставляет notified=True только после закрытия окна.
        """
        import threading
        from loguru import logger
        def run_pyqt_notification():
            try:
                try:
                    from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout
                    from PyQt6.QtCore import QTimer, Qt
                except ImportError as e:
                    logger.error(f"[PyQt6] PyQt6 is not installed: {e}")
                    return
                import sys
                import os
                # Проверка на headless (нет DISPLAY)
                if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
                    logger.error("[PyQt6] No DISPLAY found (headless mode), cannot show notification window.")
                    return
                class ReminderDialog(QDialog):
                    def __init__(self, title, message, on_close):
                        super().__init__()
                        self.setWindowTitle(title)
                        self.setWindowModality(Qt.WindowModality.ApplicationModal)
                        self.setFixedSize(400, 200)
                        layout = QVBoxLayout()
                        label = QLabel(message)
                        label.setWordWrap(True)
                        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        layout.addWidget(label)
                        btn = QPushButton("Закрыть")
                        btn.clicked.connect(self.accept)
                        layout.addWidget(btn)
                        self.setLayout(layout)
                        self.beep_timer = QTimer(self)
                        self.beep_timer.setInterval(300 * 1000)  # 5 минут
                        self.beep_timer.timeout.connect(self.do_beep)
                        self.do_beep()  # Первый beep сразу
                        self.beep_timer.start()
                        self._on_close = on_close
                    def do_beep(self):
                        try:
                            QApplication.beep()
                            logger.info("[PyQt6] Beep triggered for reminder window.")
                        except Exception as e:
                            logger.error(f"[PyQt6] Beep error: {e}")
                    def accept(self):
                        self.beep_timer.stop()
                        logger.info("[PyQt6] Reminder window closed by user.")
                        if self._on_close:
                            self._on_close()
                        super().accept()
                # QApplication instance (singleton)
                app = QApplication.instance() or QApplication([])
                closed = threading.Event()
                def on_close():
                    closed.set()
                dlg = ReminderDialog(title, message, on_close)
                dlg.show()
                logger.info(f"[PyQt6] Reminder window shown: {title} - {message}")
                app.exec()  # Блокирует только этот поток
                closed.wait()
            except Exception as e:
                logger.error(f"[PyQt6] Failed to show notification: {e}")
        def after_close():
            if reminder is not None and db is not None:
                try:
                    reminder.notified = True
                    db.commit()
                    logger.info(f"[REMINDER] Marked reminder id={reminder.id} as notified after PyQt6 window closed.")
                except Exception as e:
                    logger.error(f"[REMINDER] DB error after PyQt6 window: {e}")
        # Запуск в отдельном потоке
        threading.Thread(target=lambda: [run_pyqt_notification(), after_close()], daemon=True).start()

    def show_notification(self, title: str, message: str):
        try:
            notification.notify(
                title=title,
                message=message,
                app_name="Todo MCP",
                timeout=10
            )
            logger.info(f"Notification shown: {title} - {message}")
        except Exception as e:
            logger.error(f"Failed to show notification: {e}")

    def run(self):
        self.running = True
        pid = os.getpid()
        set_worker_pid(pid)
        logger.info(f"Worker process started with PID {pid}")
        try:
            with SessionLocal() as db:
                while self.running:
                    self.check_reminders(db)
                    time.sleep(self.poll_interval)
        finally:
            # Очищаем PID только если он наш
            meta = get_or_create_meta()
            if meta.worker_pid == pid:
                set_worker_pid(None)
            logger.info(f"Worker process with PID {pid} stopped and PID cleared.")

    def check_reminders(self, db: Session):
        now = datetime.utcnow()
        reminders = db.query(Reminder).filter(Reminder.remind_at <= now, Reminder.notified == False).all()
        for reminder in reminders:
            # Получаем описание задачи для уведомления
            task = db.query(Task).filter_by(id=reminder.task_id).first()
            task_desc = task.description if task else f"Task {reminder.task_id}"
            logger.info(f"[REMINDER] Triggered: id={reminder.id}, task_id={reminder.task_id}, time={reminder.remind_at}")
            # Для каждого напоминания отдельный поток с окном и beep
            threading.Thread(
                target=self.show_custom_notification,
                args=("Напоминание о задаче", task_desc, reminder, db),
                daemon=True
            ).start()
            # Помечаем как notified сразу после старта потока, чтобы не было дублирования
            reminder.notified = True
            db.commit()
            logger.info(f"[REMINDER] Marked reminder id={reminder.id} as notified after thread start.")

def run_worker_process():
    worker = Worker()
    worker.run()

def start_worker_as_process():
    p = multiprocessing.Process(target=run_worker_process, daemon=True)
    p.start()
    return p.pid 