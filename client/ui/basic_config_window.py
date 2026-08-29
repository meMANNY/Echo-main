import logging
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,
    QMessageBox, QFileDialog, QDesktopWidget
)
from utils.constants import SHARE_FOLDER_PATH, RECV_FOLDER_PATH

logger = logging.getLogger(__name__)

class BasicConfigWindow(QWidget):
    def __init__(self, adapter, chosen_uname):
        super().__init__()
        self.adapter = adapter
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

        #share folder row

        layout.addWidget(QLabel("Select the folder to share:"))
        h_share = QHBoxLayout()
        self.share_input = QLineEdit(str(SHARE_FOLDER_PATH))
        btn_browse_share = QPushButton("Browse...")
        btn_browse_share.clicked.connect(self.browse_share)
        h_share.addWidget(self.share_input)
        h_share.addWidget(btn_browse_share)
        layout.addLayout(h_share)

        #receive/download folder row

        layout.addWidget(QLabel("Select the folder to receive files:"))
        h_dl = QHBoxLayout()
        self.dl_input = QLineEdit(str(RECV_FOLDER_PATH))
        btn_browse_dl = QPushButton("Browse...")
        btn_browse_dl.clicked.connect(self.browse_dl)
        h_dl.addWidget(self.dl_input)
        h_dl.addWidget(btn_browse_dl)
        layout.addLayout(h_dl)

        #status and finish button

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666666; font-style: italic; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.btn_finish = QPushButton("Finish")
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

    def on_finish(self):
        server_ip = self.ip_input.text().strip()
        share_path = self.share_input.text().strip()
        dl_path = self.dl_input.text().strip()

        if not server_ip:
            QMessageBox.warning(self, "Input Error", "Server IP address cannot be empty.")
            return

        core = self.adapter.core
        core.settings["uname"] = self.chosen_uname
        core.settings["server_ip"] = server_ip
        core.settings["share_folder_path"] = share_path
        core.settings["downloads_folder_path"] = dl_path
        core.save_settings(core.settings)

        # ui state during connection
        self.btn_finish.setEnabled(False)
        self.status_label.setText("Connecting to server and registering... Please wait.")

        # connect + register + publish, off the GUI thread, through the adapter
        self.adapter.connect_and_register_async(
            self.chosen_uname, server_ip, self.on_registration_finished
        )

    def on_registration_finished(self, success, err_msg):
        self.btn_finish.setEnabled(True)
        if success:
            logger.info("Registration successful. Opening the main window.")
            from client.ui.echo_main_window import EchoMainWindow
            self.main_window = EchoMainWindow(self.adapter)
            self.main_window.show()
            self.close()  # Close the configuration window
        else:
            self.status_label.setText(f"Error: {err_msg}")
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog(err_msg, parent=self).exec_()
