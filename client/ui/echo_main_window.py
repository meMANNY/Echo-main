import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QListWidget, QTextEdit, QLineEdit, QPushButton, 
    QTreeWidget, QTreeWidgetItem, QLabel, QFrame, 
    QScrollArea, QDesktopWidget
)
logger = logging.getLogger(__name__)

class EchoMainWindow(QMainWindow):
    def __init__(self,adapter):
        super().__init__()
        self.adapter = adapter
        self.selected_peer = None
        self.init_ui()