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

        sub_label = QLabel("Please enter your username to continue:")
        sub_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub_label)

        self.uname_input = QLineEdit()
        self.uname_input.setPlaceholderText("Enter username")
        self.uname_input.returnPressed.connect(self.on_continue)  # Connect the return key to the continue action
        layout.addWidget(self.uname_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;font-size: 11px;")
        self.error_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.error_label)

        self.btn_continue = QPushButton("Continue")
        self.btn_continue.setFixedHeight(34)
        self.btn_continue.clicked.connect(self.on_continue)
        layout.addWidget(self.btn_continue)

        self.setLayout(layout)
        self.center_on_screen()

    def center_on_screen(self):
        frame_geometry = self.frameGeometry()
        center_point = self.screen().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def on_continue(self):
        username = self.uname_input.text().strip()
        if not username:
            self.error_label.setText("Username cannot be empty.")
            return

        if " " in username:
            self.error_label.setText("Username cannot contain spaces.")
            return


        # If all validations pass, proceed to the next window
        self.error_label.setText("")  # Clear any previous error messages
        logger.info(f"Username '{username}' accepted. Proceeding to basic configuration window.")
        self.config_window = BasicConfigWindow(self.core, username)
        self.config_window.show()
        self.close()  # Close the start window after opening the basic config window
        

