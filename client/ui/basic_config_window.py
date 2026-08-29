import logging
from pathlib import Path
from PyQt5.QtCore import Qt,QThread,pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,
    QMessageBox, QFileDialog, QDesktopWidget
)
from utils.constants import SHARE_FOLDER_PATH, RECV_FOLDER_PATH

logger = logging.getLogger(__name__)

class RegistrationWorker(QThread):
    """Runs network connection and registration off the main thread to avoid blocking the UI."""

    #(success: bool, error_message: str)
    finished = pyqtSignal(bool, str) #should not be defined in the __init__

    def __init__(self, core, username,server_ip):
        super().__init__()
        self.core = core
        self.username = username
        self.server_ip = server_ip

    def run(self):
        #connect to central server
        if not self.core.connect_to_server(self.server_ip):
            self.finished.emit(False, "Failed to connect to the server. Please check the IP address and try again.")
            return

        #register the user
        if not self.core.register(self.username):
            self.finished.emit(False, "Registration failed. Please try again.")
            return

        self.core.publish_share_data()
        self.finished.emit(True, "Registration successful!")

class BasicConfigWindow(QWidget):
    def __init__(self,core,chosen_uname):
        super().__init__()
        self.core = core
        self.chosen_uname = chosen_uname
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Echo - Initial Configuration")
        self.setFixedSize(480,320)  # Set a fixed size for the window

        layout = QVBoxLayout()
        layout.setContentsMargins(25, 20, 25, 20)  
        layout.setSpacing(10)

        title = QLabel(f"Initial Configuration for {self.chosen_uname}")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        #server IP
        layout.addWidget(QLabel("Enter the server IP address:"))
        self.ip_input = QLineEdit("127.0.0.1")
        layout.addWidget(self.ip_input)

        layout.addWidget(QLabel("Select the folder to share:"))
        h_share = QHBoxLayout()
        self.share_input = QLineEdit(str(SHARE_FOLDER_PATH))
        btn_browse_share = QPushButton("Browse...")
        btn_browse_share.clicked.connect(self.browse_share)
        h_share.addWidget(self.share_input)
        h_share.addWidget(btn_browse_share)
        layout.addLayout(h_share)

        