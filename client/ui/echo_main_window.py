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

    