import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

logger = logging.getLogger(__name__)


class ErrorDialog(QDialog):
    """Reusable error surface (5.2.8): a message + Close, with an optional
    'Open Settings' button for settings-fixable errors (e.g. a wrong server
    IP). Built early so every later screen has a real error surface instead of
    a bare print()/QMessageBox. Unlike Drizzle's fatal sys.exit() path, showing
    an error never kills the app — the user reads it and carries on."""

    def __init__(self, message: str, *, title: str = "Something went wrong",
                 on_open_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        self._on_open_settings = on_open_settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        icon = QLabel("⚠")  # warning sign
        icon.setStyleSheet("font-size: 26px;")
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)

        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(msg)

        buttons = QHBoxLayout()
        buttons.addStretch()
        if on_open_settings is not None:
            btn_settings = QPushButton("Open Settings")
            btn_settings.clicked.connect(self._open_settings)
            buttons.addWidget(btn_settings)
        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)

    def _open_settings(self):
        self.accept()
        if self._on_open_settings is not None:
            self._on_open_settings()
