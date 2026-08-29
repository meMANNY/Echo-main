import logging
from pathlib import Path
from PyQt5.QtCore import Qt,QThread,pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,
    QMessageBox, QFileDialog, QDesktopWidget
)
from utils.constants import SHARE_FOLDER_PATH, RECV_FOLDER_PATH

logger = logging.getLogger(__name__)

