import logging
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QCheckBox, QGroupBox, QMessageBox
)
logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    User Preferences & Settings Dialog (5.2.4).
    Edits server IP, folder paths and notification preference. Saving goes
    through adapter.update_settings(), which persists once and — if the share
    folder moved — re-points the watcher and republishes (5.3.8).

    After exec_(), `changed` holds the keys that actually changed, so the main
    window can react (e.g. reconnect when server_ip changed).
    """
    def __init__(self, adapter, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.changed: set = set()
        self.init_ui()

    def init_ui(self):
        s = self.adapter.settings
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
        self.lbl_uname = QLabel(f"<b>{self.adapter.my_uname}</b>")
        h_uname.addWidget(self.lbl_uname)
        h_uname.addStretch()
        v_ident.addLayout(h_uname)
        h_ip = QHBoxLayout()
        h_ip.addWidget(QLabel("Server IP:"))
        self.input_server_ip = QLineEdit(s.get("server_ip", "127.0.0.1"))
        h_ip.addWidget(self.input_server_ip)
        v_ident.addLayout(h_ip)
        layout.addWidget(group_identity)
        # 2. Folder Paths
        group_folders = QGroupBox("Folder Locations")
        v_folders = QVBoxLayout(group_folders)
        v_folders.addWidget(QLabel("Share Folder (Files you share):"))
        h_share = QHBoxLayout()
        self.input_share = QLineEdit(s.get("share_folder_path", ""))
        btn_browse_share = QPushButton("Browse...")
        btn_browse_share.clicked.connect(self._browse_share)
        h_share.addWidget(self.input_share)
        h_share.addWidget(btn_browse_share)
        v_folders.addLayout(h_share)
        v_folders.addWidget(QLabel("Downloads Folder (Saved files):"))
        h_dl = QHBoxLayout()
        self.input_dl = QLineEdit(s.get("downloads_folder_path", ""))
        btn_browse_dl = QPushButton("Browse...")
        btn_browse_dl.clicked.connect(self._browse_dl)
        h_dl.addWidget(self.input_dl)
        h_dl.addWidget(btn_browse_dl)
        v_folders.addLayout(h_dl)
        layout.addWidget(group_folders)
        # 3. Preferences
        self.chk_notifications = QCheckBox("Show Desktop Notifications")
        self.chk_notifications.setChecked(s.get("show_notifications", True))
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

    def _browse_share(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Share Folder", self.input_share.text())
        if folder:
            self.input_share.setText(folder)

    def _browse_dl(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Downloads Folder", self.input_dl.text())
        if folder:
            self.input_dl.setText(folder)

    def _save_settings(self):
        server_ip = self.input_server_ip.text().strip()
        share_path = self.input_share.text().strip()
        dl_path = self.input_dl.text().strip()

        if not server_ip:
            QMessageBox.warning(self, "Input Error", "Server IP address cannot be empty.")
            return
        if not share_path or not Path(share_path).is_dir():
            QMessageBox.warning(self, "Input Error", "The share folder must be an existing folder.")
            return
        if not dl_path:
            QMessageBox.warning(self, "Input Error", "Downloads folder path cannot be empty.")
            return
        try:
            Path(dl_path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Input Error", f"Can't use that downloads folder: {e}")
            return

        self.changed = self.adapter.update_settings({
            "server_ip": server_ip,
            "share_folder_path": share_path,
            "downloads_folder_path": dl_path,
            "show_notifications": self.chk_notifications.isChecked(),
        })
        self.accept()
