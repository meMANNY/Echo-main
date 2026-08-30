import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFileDialog, QCheckBox, QGroupBox, QMessageBox
)
logger = logging.getLogger(__name__)
class SettingsDialog(QDialog):
    """
    User Preferences & Settings Dialog (5.2.4).
    Allows updating Server IP, folder paths, and notification preferences.
    """
    def __init__(self, adapter, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.core = adapter.core
        self.init_ui()
    def init_ui(self):
        self.setWindowTitle("Echo — Settings")
        self.setFixedSize(500, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)
        # 1. Connection & Identity
        group_identity = QGroupBox("Network & Identity")
        v_ident = QVBoxLayout(group_identity)
        
        h_uname = QHBoxLayout()
        h_uname.addWidget(QLabel("Username:"))
        self.lbl_uname = QLabel(f"<b>{self.core.settings.get('uname', '')}</b>")
        h_uname.addWidget(self.lbl_uname)
        h_uname.addStretch()
        v_ident.addLayout(h_uname)
        h_ip = QHBoxLayout()
        h_ip.addWidget(QLabel("Server IP:"))
        self.input_server_ip = QLineEdit(self.core.settings.get("server_ip", "127.0.0.1"))
        h_ip.addWidget(self.input_server_ip)
        v_ident.addLayout(h_ip)
        layout.addWidget(group_identity)
        # 2. Folder Paths
        group_folders = QGroupBox("Folder Locations")
        v_folders = QVBoxLayout(group_folders)
        v_folders.addWidget(QLabel("Share Folder (Files you share):"))
        h_share = QHBoxLayout()
        self.input_share = QLineEdit(self.core.settings.get("share_folder_path", ""))
        btn_browse_share = QPushButton("Browse...")
        btn_browse_share.clicked.connect(self._browse_share)
        h_share.addWidget(self.input_share)
        h_share.addWidget(btn_browse_share)
        v_folders.addLayout(h_share)
        v_folders.addWidget(QLabel("Downloads Folder (Saved files):"))
        h_dl = QHBoxLayout()
        self.input_dl = QLineEdit(self.core.settings.get("downloads_folder_path", ""))
        btn_browse_dl = QPushButton("Browse...")
        btn_browse_dl.clicked.connect(self._browse_dl)
        h_dl.addWidget(self.input_dl)
        h_dl.addWidget(btn_browse_dl)
        v_folders.addLayout(h_dl)
        layout.addWidget(group_folders)
        # 3. Preferences
        self.chk_notifications = QCheckBox("Show Desktop Notifications")
        self.chk_notifications.setChecked(self.core.settings.get("show_notifications", True))
        layout.addWidget(self.chk_notifications)
        # 4. Buttons
        h_buttons = QHBoxLayout()
        h_buttons.addStretch()
        
        self.btn_save = QPushButton("Save Settings")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._save_settings)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        h_buttons.addWidget(self.btn_save)
        h_buttons.addWidget(self.btn_cancel)
        layout.addLayout(h_buttons)