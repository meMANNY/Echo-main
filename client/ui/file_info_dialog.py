import logging
from PyQt5.QtWidgets import QApplication, QFormLayout, QMainWindow, QDesktopWidget, QVBoxLayout
from PyQt5.QtCore import Qt
from utils.helpers import convert_size

logger = logging.getLogger(__name__)


class FileInfoDialog(QMainWindow):
    """Dialog to display file information.
    Read-only details view of a selected DirData Node."""

    def __init__(self, item_data: dict,parent=None):
        super().__init__()
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

        

