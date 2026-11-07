import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView,
)
from utils.helpers import convert_size
logger = logging.getLogger(__name__)


class FileSearchDialog(QDialog):
    """
    Network-wide file search dialog (5.2.5).
    Queries the central index for matching files across all online peers,
    then lets the user download a hit or jump to its owner (4.4.2).
    """
    def __init__(self, adapter, on_go_to_owner=None, on_download=None, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.on_go_to_owner = on_go_to_owner  # callable(owner)
        self.on_download = on_download        # callable(owner, DirData) -> bool (started?)
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
        self.input_query.textChanged.connect(self._on_query_changed)
        self.btn_search = QPushButton("Search")
        self.btn_search.setFixedWidth(90)
        self.btn_search.setEnabled(False)  # empty query -> no request (4.4.1)
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
        self.btn_download = QPushButton("Download")
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self.on_download_clicked)
        self.btn_go_to_owner = QPushButton("Go to Owner")
        self.btn_go_to_owner.setEnabled(False)
        self.btn_go_to_owner.clicked.connect(self.on_go_to_owner_clicked)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        h_bottom.addWidget(self.btn_download)
        h_bottom.addWidget(self.btn_go_to_owner)
        h_bottom.addStretch()
        h_bottom.addWidget(self.btn_close)
        layout.addLayout(h_bottom)

    def _on_query_changed(self, text: str):
        self.btn_search.setEnabled(bool(text.strip()))

    def on_search(self):
        query = self.input_query.text().strip()
        if not query or not self.btn_search.isEnabled():
            return  # empty query or a search already in flight: send nothing
        self.btn_search.setEnabled(False)
        self.lbl_status.setText(f"Searching for '{query}'...")
        self.table_results.setRowCount(0)
        self.results_data = []
        self.on_selection_changed()
        self.adapter.search_async(query, on_success=self.on_search_success, on_error=self.on_search_error)

    def on_search_success(self, results):
        self._on_query_changed(self.input_query.text())
        query = self.input_query.text().strip()
        if results is None:
            # None = the request itself failed (4.4.1), distinct from "no hits".
            self.lbl_status.setText("Search failed — the server connection may be down.")
            return
        self.results_data = results
        if not self.results_data:
            self.lbl_status.setText(f"No results found for '{query}'.")
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
            item_name = QTableWidgetItem(name + ("/" if is_dir else ""))
            item_size = QTableWidgetItem("—" if is_dir else convert_size(size or 0))
            item_type = QTableWidgetItem("Folder" if is_dir else "File")
            item_path = QTableWidgetItem(path)
            # The full result rides on column 0 (Qt.UserRole), like the share tree.
            item_owner.setData(Qt.UserRole, res)
            self.table_results.setItem(row, 0, item_owner)
            self.table_results.setItem(row, 1, item_name)
            self.table_results.setItem(row, 2, item_size)
            self.table_results.setItem(row, 3, item_type)
            self.table_results.setItem(row, 4, item_path)

    def on_search_error(self, error_msg: str):
        self._on_query_changed(self.input_query.text())
        self.lbl_status.setText(f"Search failed: {error_msg}")
        logger.error(f"Search error: {error_msg}")

    def _selected_result(self):
        row = self.table_results.currentRow()
        if row < 0 or not self.table_results.selectedItems():
            return None
        item = self.table_results.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def on_selection_changed(self):
        res = self._selected_result()
        self.btn_go_to_owner.setEnabled(res is not None)
        can_download = (
            res is not None
            and self.on_download is not None
            and self.adapter.is_online(res.get("owner"))
        )
        self.btn_download.setEnabled(can_download)

    def on_download_clicked(self):
        res = self._selected_result()
        if not res or not self.on_download:
            return
        owner, data = res.get("owner"), res.get("data", {})
        if self.on_download(owner, data):
            self.lbl_status.setText(f"Downloading '{data.get('name')}' from {owner}. Progress is shown in Active Transfers.")
        else:
            self.lbl_status.setText(f"Couldn't start the download of '{data.get('name')}' (is {owner} still online?).")

    def on_go_to_owner_clicked(self):
        res = self._selected_result()
        if not res:
            return
        owner = res.get("owner")
        logger.info(f"Navigating to owner: '{owner}' from search result")
        if self.on_go_to_owner and owner:
            self.on_go_to_owner(owner)
            self.accept()
