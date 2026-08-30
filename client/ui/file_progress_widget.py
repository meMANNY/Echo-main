import logging
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QFrame
from utils.helpers import convert_size
from utils.types import TransferStatus
logger = logging.getLogger(__name__)

class FileProgressWidget(QFrame):
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

        # Bottom row: Progress Bar + Pause/Resume Button
        h_bottom = QHBoxLayout()
        self.progress_bar = QProgressBar()
        # Scale to basis points (0-10000) for smooth visual motion
        self.progress_bar.setMaximum(10000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat(f"0 B / {convert_size(self.total_size)}")
        h_bottom.addWidget(self.progress_bar)
        if self.allow_pause:
            self.btn_toggle = QPushButton("⏸ Pause" if self.status == TransferStatus.DOWNLOADING else "▶ Resume")
            self.btn_toggle.setFixedWidth(75)
            self.btn_toggle.clicked.connect(self._on_toggle_clicked)
            h_bottom.addWidget(self.btn_toggle)
        else:
            self.btn_toggle = None
        layout.addLayout(h_bottom)

    def _on_toggle_clicked(self):
        if self.status == TransferStatus.DOWNLOADING:
            self.status = TransferStatus.PAUSED
            self.btn_toggle.setText("▶ Resume")
            self.lbl_speed_eta.setText("Pausing...")
            self.pause_requested.emit(self.transfer_key)
        else:
            self.status = TransferStatus.DOWNLOADING
            self.btn_toggle.setText("⏸ Pause")
            self.lbl_speed_eta.setText("Resuming...")
            self.resume_requested.emit(self.transfer_key)

    def update_progress(self,progress_data: dict):
        """
        Update the progress bar and speed/ETA label based on the provided progress data.
        """

        received = progress_data.get("progress", 0)
        total = progress_data.get("total", self.total_size)
        self.status = progress_data.get("status", self.status)
        # Update basis points value
        if total > 0:
            basis_points = int((received / total) * 10000)
            self.progress_bar.setValue(min(basis_points, 10000))
            self.progress_bar.setFormat(f"{convert_size(received)} / {convert_size(total)} ({basis_points/100:.1f}%)")
        # Format Speed and ETA
        speed_bps = progress_data.get("speed_bps")
        eta_sec = progress_data.get("eta_seconds")
        
        if self.status == TransferStatus.PAUSED:
            self.lbl_speed_eta.setText("Paused")
            if self.btn_toggle:
                self.btn_toggle.setText("▶ Resume")
        elif speed_bps is not None and speed_bps > 0:
            speed_str = f"{convert_size(speed_bps)}/s"
            eta_str = f"{int(eta_sec)}s left" if eta_sec is not None else ""
            self.lbl_speed_eta.setText(f"{speed_str} | {eta_str}" if eta_str else speed_str)
            if self.btn_toggle:
                self.btn_toggle.setText("⏸ Pause")