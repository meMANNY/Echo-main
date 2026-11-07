import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit, QHBoxLayout,QMessageBox
)

logger = logging.getLogger(__name__)


def validate_username(username: str):
    """Client-side username check shared by the first-run windows (5.2.1).
    Returns an error message, or None if the name is acceptable. The server
    still has the final say (USER_EXISTS); this just fails fast and politely."""
    if not username:
        return "Username cannot be empty."
    if any(ch.isspace() for ch in username):
        return "Username cannot contain spaces."
    if ":" in username:
        return "Username cannot contain ':'."  # ':' separates owner:path in transfer keys
    return None

class StartWindow(QWidget):
    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter
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
        error = validate_username(username)
        if error:
            self.error_label.setText(error)
            return

        # If all validations pass, proceed to the next window
        self.error_label.setText("")  # Clear any previous error messages
        logger.info(f"Username '{username}' accepted. Proceeding to basic configuration window.")
        from client.ui.basic_config_window import BasicConfigWindow
        self.config_window = BasicConfigWindow(self.adapter, username)
        self.config_window.show()
        self.close()  # Close the start window after opening the basic config window
        

