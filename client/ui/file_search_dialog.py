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

    def on_search(self):
        query = self.input_query.text().strip()
        if not query:
            QMessageBox.warning(self, "Input Error", "Please enter a search term.")
            return
        self.btn_search.setEnabled(False)
        self.lbl_status.setText(f"Searching for '{query}'...")
        self.table_results.setRowCount(0)
        self.btn_go_to_owner.setEnabled(False)
        self.adapter.search_async(query, on_success=self.on_search_success, on_error=self.on_search_error)

    def on_search_success(self, results: list):
        self.btn_search.setEnabled(True)
        self.results_data = results or []
        if not self.results_data:
            self.lbl_status.setText(f"No results found for '{self.input_query.text().strip()}'.")
            return
        self.lbl_status.setText(f"Found {len(self.results_data)} match(es):")
        self.table_results.setRowCount(len(self.results_data))
        for row, res in enumerate(self.results_data):
            owner = res.get("owner", "Unknown")
            data = res.get("data", {})
            name = data.get("name", "Unknown")
            size = data.get("size", 0)
            is_dir = data.get("type") == "directory"
            path = data.get("path", "/")
            item_owner = QTableWidgetItem(owner)
            item_name = QTableWidgetItem(name)
            item_size = QTableWidgetItem("—" if is_dir else convert_size(size))
            item_type = QTableWidgetItem("Folder" if is_dir else "File")
            item_path = QTableWidgetItem(path)
            # Store the full result dict on column 0 for retrieval
            item_owner.setData(Qt.UserRole, res)
            self.table_results.setItem(row, 0, item_owner)
            self.table_results.setItem(row, 1, item_name)
            self.table_results.setItem(row, 2, item_size)
            self.table_results.setItem(row, 3, item_type)
            self.table_results.setItem(row, 4, item_path)

    def on_search_error(self, error_msg: str):
        self.btn_search.setEnabled(True)
        self.lbl_status.setText(f"Search failed: {error_msg}")
        logger.error(f"Search error: {error_msg}")
    def on_selection_changed(self):
        selected_rows = self.table_results.selectedItems()
        self.btn_go_to_owner.setEnabled(len(selected_rows) > 0)
    def on_go_to_owner_clicked(self):
        selected_items = self.table_results.selectedItems()
        if not selected_items:
            return
        res = selected_items[0].data(Qt.UserRole)
        if not res:
            return
        owner = res.get("owner")
        logger.info(f"Navigating to owner: '{owner}' from search result")
        if self.on_go_to_owner and owner:
            self.on_go_to_owner(owner)
            self.accept()
