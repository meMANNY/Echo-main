import logging
from PyQt5.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from PyQt5.QtCore import Qt
from utils.helpers import convert_size

logger = logging.getLogger(__name__)


class FileInfoDialog(QDialog):
    """Read-only details view of a selected DirData node (5.2.6). Modal dialog,
    so it must subclass QDialog — that's what gives us exec_() and accept()."""

    def __init__(self, item_data: dict, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.init_ui()

    def init_ui(self):
        is_dir = self.item_data.get(("type")) == "directory"
        name = self.item_data.get("name", "Unknown")
        self.setWindowTitle(f"Properties - {name}")
        self.setFixedSize(440, 260)  # Set a fixed size for the dialog
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)

        name_lbl = QLabel(f"<b>{name}</b>")
        path_lbl = QLabel(self.item_data.get("path", "/"))
        type_lbl = QLabel("Folder" if is_dir else "File")
        raw_size = self.item_data.get("size", 0)
        size_lbl = QLabel("—" if is_dir else f"{convert_size(raw_size)} ({raw_size:,} bytes)")
        raw_hash = self.item_data.get("hash")
        hash_lbl = QLabel("—" if is_dir else (raw_hash if raw_hash else "Unverified (computed on first download)"))
        hash_lbl.setWordWrap(True)
        form.addRow("Name:", name_lbl)
        form.addRow("Relative Path:", path_lbl)
        form.addRow("Type:", type_lbl)
        form.addRow("Size:", size_lbl)
        form.addRow("SHA-1 Hash:", hash_lbl)
        layout.addLayout(form)
        layout.addStretch()

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)



