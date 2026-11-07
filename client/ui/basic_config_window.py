import logging
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,
    QMessageBox, QFileDialog, QDesktopWidget
)
from utils.constants import SHARE_FOLDER_PATH, RECV_FOLDER_PATH
from client.ui.start_window import validate_username

logger = logging.getLogger(__name__)


class BasicConfigWindow(QWidget):
    """First-run step 2 (5.2.2): server IP + share/downloads folders, then
    connect+register in a worker. The three outcomes surface right here:
      success       -> open the main window
      USER_EXISTS   -> stay, highlight the (editable) username to rename
      connect fails -> ErrorDialog, stay so the IP can be fixed
    Nothing is persisted as the username until registration succeeds, so a
    failed first run is still a first run on the next launch."""

    def __init__(self, adapter, chosen_uname):
        super().__init__()
        self.adapter = adapter
        self.chosen_uname = chosen_uname
        self.init_ui()

    def init_ui(self):
        s = self.adapter.settings
        self.setWindowTitle("Echo - Initial Configuration")
        self.setFixedSize(480, 380)

        layout = QVBoxLayout()
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(8)

        title = QLabel("Initial Configuration")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        # username (editable here so a USER_EXISTS can be fixed in place)
        layout.addWidget(QLabel("Username:"))
        self.uname_input = QLineEdit(self.chosen_uname)
        layout.addWidget(self.uname_input)

        # server IP
        layout.addWidget(QLabel("Server IP address:"))
        self.ip_input = QLineEdit(s.get("server_ip") or "127.0.0.1")
        layout.addWidget(self.ip_input)

        # share folder row
        layout.addWidget(QLabel("Folder to share:"))
        h_share = QHBoxLayout()
        self.share_input = QLineEdit(s.get("share_folder_path") or str(SHARE_FOLDER_PATH))
        btn_browse_share = QPushButton("Browse...")
        btn_browse_share.clicked.connect(self.browse_share)
        h_share.addWidget(self.share_input)
        h_share.addWidget(btn_browse_share)
        layout.addLayout(h_share)

        # receive/download folder row
        layout.addWidget(QLabel("Folder to save downloads in:"))
        h_dl = QHBoxLayout()
        self.dl_input = QLineEdit(s.get("downloads_folder_path") or str(RECV_FOLDER_PATH))
        btn_browse_dl = QPushButton("Browse...")
        btn_browse_dl.clicked.connect(self.browse_dl)
        h_dl.addWidget(self.dl_input)
        h_dl.addWidget(btn_browse_dl)
        layout.addLayout(h_dl)

        # status and finish button
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #666666; font-style: italic; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.btn_finish = QPushButton("Finish")
        self.btn_finish.setDefault(True)
        self.btn_finish.setFixedHeight(36)
        self.btn_finish.clicked.connect(self.on_finish)
        layout.addWidget(self.btn_finish)

        self.setLayout(layout)
        self.center_on_screen()

    def center_on_screen(self):
        frame_geometry = self.frameGeometry()
        center_point = QDesktopWidget().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def browse_share(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Share Folder", self.share_input.text())
        if folder:
            self.share_input.setText(folder)

    def browse_dl(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder", self.dl_input.text())
        if folder:
            self.dl_input.setText(folder)

    def _set_busy(self, busy: bool):
        for w in (self.btn_finish, self.uname_input, self.ip_input, self.share_input, self.dl_input):
            w.setEnabled(not busy)

    def on_finish(self):
        username = self.uname_input.text().strip()
        server_ip = self.ip_input.text().strip()
        share_path = self.share_input.text().strip()
        dl_path = self.dl_input.text().strip()

        error = validate_username(username)
        if error:
            QMessageBox.warning(self, "Input Error", error)
            self.uname_input.setFocus()
            return
        if not server_ip:
            QMessageBox.warning(self, "Input Error", "Server IP address cannot be empty.")
            return
        try:
            Path(share_path).mkdir(parents=True, exist_ok=True)
            Path(dl_path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Input Error", f"Can't use those folders: {e}")
            return

        # Folders + server go into settings now; the username is written by
        # core.register() only on success, so an abandoned first run (taken
        # name, unreachable server) still counts as a first run next launch.
        self.adapter.update_settings({
            "server_ip": server_ip,
            "share_folder_path": share_path,
            "downloads_folder_path": dl_path,
        })
        self.chosen_uname = username

        self._set_busy(True)
        self.status_label.setText("Connecting to server and registering... Please wait.")

        # connect + register + publish, off the GUI thread, through the adapter
        self.adapter.connect_and_register_async(username, server_ip, self.on_registration_finished)

    def on_registration_finished(self, success: bool, message: str, reason: str):
        self._set_busy(False)
        if success:
            logger.info("Registration successful. Opening the main window.")
            from client.ui.echo_main_window import EchoMainWindow
            self.main_window = EchoMainWindow(self.adapter)
            self.main_window.center_on_screen()
            self.main_window.show()
            self.close()
            return

        self.status_label.setText(f"Error: {message}")
        if reason == "user_exists":
            QMessageBox.information(self, "Username taken", message)
            self.uname_input.setFocus()
            self.uname_input.selectAll()
        else:
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog(message, title="Couldn't connect", parent=self).exec_()
            self.ip_input.setFocus()
