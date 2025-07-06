from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QPushButton
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QSoundEffect
from loguru import logger
import sys
import uuid
import threading


class NotificationWindow(QWidget):
    def __init__(
            self, 
            message: str, 
            sound_path: str, 
            on_close_callback,
            timeout: int,
            sound_volume: float
        ):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Уведомление")

        self.layout = QVBoxLayout()
        self.label = QLabel(message)
        self.close_btn = QPushButton("Закрыть")

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.close_btn)
        self.setLayout(self.layout)

        self.close_btn.clicked.connect(self.close)

        self.sound = QSoundEffect()
        self.sound.setSource(QUrl.fromLocalFile(sound_path))
        self.sound.setVolume(sound_volume)

        self.timer = QTimer()
        self.timer.setInterval(timeout)
        self.timer.timeout.connect(self.play_sound)

        self.on_close_callback = on_close_callback

        logger.info(f"Создано уведомление: {message}")

    def showEvent(self, event):
        self.play_sound()
        self.timer.start()
        super().showEvent(event)

    def play_sound(self):
        logger.info("Воспроизведение звука для уведомления")
        self.sound.play()

    def closeEvent(self, event):
        logger.info("Уведомление закрыто пользователем")
        self.timer.stop()
        self.on_close_callback(self)
        super().closeEvent(event)


class NotificationManager:
    """
    Менеджер уведомлений с поддержкой singleton.

    Особенности:
    - Поддержка singleton-режима через параметр singleton=True.
    - Потокобезопасная реализация singleton (threading.Lock).
    - При singleton=True повторные вызовы возвращают один и тот же экземпляр.
    - Первый вызов с singleton=True определяет sound_path, последующие игнорируют sound_path.
    - Можно создавать отдельные экземпляры без singleton.

    Аргументы конструктора:
        sound_path (str): Путь к звуковому файлу для уведомлений.
        singleton (bool, optional): Если True — используется singleton-экземпляр менеджера.

    Пример использования:
        manager = NotificationManager(sound_path="/path/to/sound.wav", singleton=True)
        manager2 = NotificationManager(sound_path="/other/path.wav", singleton=True)  # manager2 is manager
        manager3 = NotificationManager(sound_path="/another/path.wav")  # отдельный экземпляр
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, sound_path: str, singleton: bool = False):
        if singleton:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init(sound_path)
                return cls._instance
        else:
            obj = super().__new__(cls)
            obj._init(sound_path)
            return obj

    def _init(self, sound_path: str):
        self.notifications = {}
        self.sound_path = sound_path

    def show_notification(self, message: str, timeout: int = 1 * 60 * 1000, sound_volume: float = 0.5):
        notification_id = str(uuid.uuid4())
        logger.info(f"Показ уведомления {notification_id}: {message}")

        def on_close(window):
            logger.info(f"Удаление уведомления {notification_id}")
            self.notifications.pop(notification_id, None)

        window = NotificationWindow(message, self.sound_path, on_close, timeout, sound_volume)
        self.notifications[notification_id] = window
        window.show()

    def close_all(self):
        logger.info("Закрытие всех уведомлений")
        for window in list(self.notifications.values()):
            window.close()
        self.notifications.clear()


# Пример использования
if __name__ == "__main__":
    app = QApplication(sys.argv)

    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sound_path = os.path.join(current_dir, "bell-notification.wav")

    # Singleton usage example
    manager = NotificationManager(sound_path, singleton=True)
    manager2 = NotificationManager("ignored-path.wav", singleton=True)  # manager2 is manager
    assert manager is manager2

    # Non-singleton usage example
    manager3 = NotificationManager(sound_path)
    assert manager3 is not manager

    manager.show_notification("Первое важное уведомление!")
    # manager3.show_notification("Второе уведомление (не singleton)")

    sys.exit(app.exec())
