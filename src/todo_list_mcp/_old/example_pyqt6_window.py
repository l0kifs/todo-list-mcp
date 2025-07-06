from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel
import sys


def main():
    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle('Пример PyQt6')
    window.setGeometry(100, 100, 300, 200)

    layout = QVBoxLayout()

    label = QLabel('Нажмите кнопку:')
    layout.addWidget(label)

    button = QPushButton('Кликни меня!')
    layout.addWidget(button)

    def on_button_clicked():
        label.setText('Кнопка нажата!')

    button.clicked.connect(on_button_clicked)

    window.setLayout(layout)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main() 