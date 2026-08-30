import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QMessageBox
)
from utils.helpers import convert_size
logger = logging.getLogger(__name__)
class FileSearchDialog(QDialog):
    """
    Network-wide file search dialog (5.2.5).
    Queries the central index for matching files across all peers.
    """
    def __init__(self, adapter, on_go_to_owner=None, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.on_go_to_owner = on_go_to_owner
        self.results_data = []
        self.init_ui()
    def init_ui(self):
        self.setWindowTitle("Echo — Search Network Files")
        self.resize(700, 450)
        self.setMinimumSize(550, 350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        # 1. Search Bar Row
        h_search = QHBoxLayout()
        self.input_query = QLineEdit()
        self.input_query.setPlaceholderText("Enter filename or keyword (e.g. report, .mp4, notes)...")
        self.input_query.returnPressed.connect(self.on_search)
        self.btn_search = QPushButton("Search")
        self.btn_search.setFixedWidth(90)
        self.btn_search.clicked.connect(self.on_search)
        h_search.addWidget(self.input_query)
        h_search.addWidget(self.btn_search)
        layout.addLayout(h_search)
        # 2. Status Label
        self.lbl_status = QLabel("Enter a keyword to search files shared across the network.")
        self.lbl_status.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.lbl_status)
        # 3. Results Table
        self.table_results = QTableWidget()
        self.table_results.setColumnCount(5)
        self.table_results.setHorizontalHeaderLabels(["Owner", "Name", "Size", "Type", "Path"])
        self.table_results.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_results.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table_results.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_results.setSelectionMode(QTableWidget.SingleSelection)
        self.table_results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_results.itemSelectionChanged.connect(self.on_selection_changed)
        self.table_results.itemDoubleClicked.connect(self.on_go_to_owner_clicked)
        layout.addWidget(self.table_results)
        # 4. Bottom Action Buttons
        h_bottom = QHBoxLayout()
        self.btn_go_to_owner = QPushButton("Go to Owner")
        self.btn_go_to_owner.setEnabled(False)
        self.btn_go_to_owner.clicked.connect(self.on_go_to_owner_clicked)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        h_bottom.addWidget(self.btn_go_to_owner)
        h_bottom.addStretch()
        h_bottom.addWidget(self.btn_close)
        layout.addLayout(h_bottom)
