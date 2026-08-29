import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QListWidget, QTextEdit, QLineEdit, QPushButton, 
    QTreeWidget, QTreeWidgetItem, QLabel, QFrame, 
    QScrollArea, QDesktopWidget
)
logger = logging.getLogger(__name__)

# ┌────────────────────────────────────────────────────────────────────────────────────────┐
# │  👤 alice  │  ● Connected (127.0.0.1)  │  [Reconnect]     [🔍 Search]   [⚙ Settings]  │
# ├─────────────────────┬────────────────────────────────────┬─────────────────────────────┤
# │ 👥 USERS            │ 💬 CHAT (with 'bob')               │ 📁 FILES & TRANSFERS        │
# │                     │                                    │                             │
# │  ● bob (online)     │ ┌────────────────────────────────┐ │ [Tree: bob's files]         │
# │  ○ charlie (2m ago) │ │ bob: Hi Alice!                 │ │ 📄 notes.pdf (2.4 MB)       │
# │                     │ │ alice: Hey Bob!                │ │ 📁 projects/                │
# │                     │ └────────────────────────────────┘ │                             │
# │                     │ [Type message...        ] [Send]   │ [Download] [Info] [Refresh] │
# │                     │                                    │ ─────────────────────────── │
# │                     │                                    │ ⬇ TRANSFERS                 │
# │                     │                                    │ [notes.pdf === 65% === ⏸]   │
# └─────────────────────┴────────────────────────────────────┴─────────────────────────────┘

class EchoMainWindow(QMainWindow):
    def __init__(self,adapter):
        super().__init__()
        self.adapter = adapter
        self.selected_peer = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"Echo — P2P File Sharing & Chat ({self.adapter.core.settings.get('uname', '')})")
        self.resize(1100, 700)
        self.setMinimumSize(900, 550)
        # Central Root Container
        root_widget = QWidget()
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(8)
        # 1. Top Status Bar
        root_layout.addWidget(self._create_top_bar())
        # 2. Main Three-Pane Horizontal Layout
        panes_layout = QHBoxLayout()
        panes_layout.setSpacing(10)
        # Left Pane (Users) - 20% width
        self.left_pane = self._create_left_pane()
        panes_layout.addWidget(self.left_pane, stretch=2)
        # Center Pane (Chat) - 45% width
        self.center_pane = self._create_center_pane()
        panes_layout.addWidget(self.center_pane, stretch=4)
        # Right Pane (Files & Downloads) - 35% width
        self.right_pane = self._create_right_pane()
        panes_layout.addWidget(self.right_pane, stretch=4)
        root_layout.addLayout(panes_layout)
        self.setCentralWidget(root_widget)
        self.center_on_screen()

    def center_on_screen(self):
        frame_geometry = self.frameGeometry()
        center_point = QDesktopWidget().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    #top bar
    def _create_top_bar(self):
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        uname = self.adapter.core.settings.get("uname", "Unknown")
        self.lbl_user_info = QLabel(f"👤 Logged in as: <b>{uname}</b>")
        layout.addWidget(self.lbl_user_info)
        layout.addSpacing(20)
        self.lbl_status = QLabel("● Connected")
        self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(self.lbl_status)
        self.btn_reconnect = QPushButton("Reconnect")
        self.btn_reconnect.setEnabled(False)  # Enabled only on disconnect
        layout.addWidget(self.btn_reconnect)
        layout.addStretch()
        self.btn_search = QPushButton("🔍 Search Network")
        layout.addWidget(self.btn_search)
        self.btn_settings = QPushButton("⚙ Settings")
        layout.addWidget(self.btn_settings)
        return bar

    #left pane
    def _create_left_pane(self):
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("<b>Network Peers</b>"))
        self.user_list = QListWidget()
        layout.addWidget(self.user_list)
        return pane

    #center pane
    def _create_center_pane(self):
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)
        self.lbl_chat_header = QLabel("<i>Select a user to chat</i>")
        layout.addWidget(self.lbl_chat_header)
        # Message History Display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display)
        # Input Box + Send Button
        h_input = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type a message...")
        self.chat_input.setEnabled(False)
        self.btn_send_chat = QPushButton("Send")
        self.btn_send_chat.setEnabled(False)
        h_input.addWidget(self.chat_input)
        h_input.addWidget(self.btn_send_chat)
        layout.addLayout(h_input)
        return pane

    #right pane
    def _create_right_pane(self) -> QWidget:
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)
        # 1. Peer Shared Files Header
        self.lbl_files_header = QLabel("<b>Shared Files</b>")
        layout.addWidget(self.lbl_files_header)
        # 2. File Tree Widget
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Name", "Size", "Type", "Hash"])
        self.file_tree.setColumnWidth(0, 180)
        layout.addWidget(self.file_tree)
        # 3. File Action Buttons
        h_buttons = QHBoxLayout()
        self.btn_download = QPushButton("Download")
        self.btn_file_info = QPushButton("File Info")
        self.btn_refresh_tree = QPushButton("Refresh")
        self.btn_download.setEnabled(False)
        self.btn_file_info.setEnabled(False)
        self.btn_refresh_tree.setEnabled(False)
        h_buttons.addWidget(self.btn_download)
        h_buttons.addWidget(self.btn_file_info)
        h_buttons.addWidget(self.btn_refresh_tree)
        layout.addLayout(h_buttons)
        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)
        # 4. Downloads & Transfers Area
        layout.addWidget(QLabel("<b>Active Transfers</b>"))
        
        self.transfers_scroll = QScrollArea()
        self.transfers_scroll.setWidgetResizable(True)
        self.transfers_scroll.setFixedHeight(140)
        
        self.transfers_container = QWidget()
        self.transfers_layout = QVBoxLayout(self.transfers_container)
        self.transfers_layout.setContentsMargins(4, 4, 4, 4)
        self.transfers_layout.setSpacing(4)
        self.transfers_layout.addStretch()  # pushes progress rows to the top
        
        self.transfers_scroll.setWidget(self.transfers_container)
        layout.addWidget(self.transfers_scroll)
        return pane


    