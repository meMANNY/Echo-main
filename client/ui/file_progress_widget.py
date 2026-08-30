import logging
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QFrame
from utils.helpers import convert_size
from utils.types import TransferStatus
logger = logging.getLogger(__name__)

class FileProgressWidget(QWidget):
    """
    A single download progress row in the transfer pane.
    Displays filename, speed/ETA, progress bar and pause/resume button.
    """

    pause_requested = pyqtSignal(str)  # Signal emitted when pause is requested, with file hash
    resume_requested = pyqtSignal(str)  # Signal emitted when resume is requested, with file hash

    def __init__(self,transfer_key: str, filename: str,total_size:int,
                initial_status: TransferStatus.DOWNLOADING, allow_pause: bool = True, parent=None):
        super().__init__(parent)
        self.transfer_key = transfer_key
        self.filename = filename
        self.total_size = total_size
        self.status = initial_status
        self.allow_pause = allow_pause
        self.init_ui()

    def init_ui(self):
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { background-color: #ffffff; border: 1px solid #dcdde1; border-radius: 4px; padding: 4px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        # Top row: Filename + Speed / ETA Label
        h_top = QHBoxLayout()
        self.lbl_filename = QLabel(f"<b>{self.filename}</b>")
        self.lbl_speed_eta = QLabel("")
        self.lbl_speed_eta.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        
        h_top.addWidget(self.lbl_filename)
        h_top.addStretch()
        h_top.addWidget(self.lbl_speed_eta)
        layout.addLayout(h_top)