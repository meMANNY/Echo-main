import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,QMessageBox
)

from client.ui.basic_config_window import BasicConfigWindow

logger = logging.getLogger(__name__)

class StartWindow(QWidget):
    def __init__(self, core):
        super().__init__()
        self.core = core
        self.init_ui()  

    def init_ui(self):
        self.setWindowTitle("Echo- Welcome")
        self.setFixedSize(380, 200)  # Set a fixed size for the window

        layout = QVBoxLayout()
        layout.setContentsMargins(30,25,30,25)  # Set margins for the layout
        layout.setSpacing(12)

        title_label = QLabel("Welcome to Echo!")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        

