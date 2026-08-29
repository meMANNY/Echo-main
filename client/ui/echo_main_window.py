import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QListWidget, QTextEdit, QLineEdit, QPushButton, 
    QTreeWidget, QTreeWidgetItem, QLabel, QFrame, 
    QScrollArea, QDesktopWidget, QListWidgetItem
)
from PyQt5.QtGui import QColor
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
        self._wire_stub_actions()
        self._connect_adapter_signals()
        self._maybe_auto_connect()
        self.user_list.currentItemChanged.connect(self._on_user_selection_changed)

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

    # --- behavior wiring -----------------------------------------------------

    def _wire_stub_actions(self):
        """Session 3: every action logs a line until its pane is wired up in a
        later session."""
        stubs = {
            self.btn_search: "Search network",
            self.btn_settings: "Open settings",
            self.btn_download: "Download selected",
            self.btn_file_info: "Show file info",
            self.btn_refresh_tree: "Refresh tree",
            self.btn_send_chat: "Send chat",
        }
        for btn, label in stubs.items():
            btn.clicked.connect(lambda _=False, l=label: logger.info(f"[stub] {l} clicked"))
        self.btn_reconnect.clicked.connect(self._begin_connect)

    def _connect_adapter_signals(self):
        """Minimal live bindings that prove the adapter seam end-to-end.
        Session 4 replaces these with the in-place diff list + full banner."""
        self.adapter.peers_changed.connect(self._on_peers_changed)
        self.adapter.connection_lost.connect(self._on_connection_lost)


    def _maybe_auto_connect(self):
        """Returning-user flow (5.1.3): if we arrived here not yet connected,
        connect+register with saved settings off the GUI thread — never block
        the first paint."""
        if not self.adapter.core.connected:
            self._begin_connect()

    def _begin_connect(self):
        s = self.adapter.core.settings
        self.lbl_status.setText("● Connecting…")
        self.lbl_status.setStyleSheet("color: #d35400; font-weight: bold;")
        self.btn_reconnect.setEnabled(False)
        self.adapter.connect_and_register_async(
            s.get("uname", ""), s.get("server_ip", ""), self._on_connect_done
        )


    def closeEvent(self, event):
        """Clean shutdown (5.1.1 step 4 / 5.4.5): stop background owners the
        adapter may hold, then close the server connection."""
        logger.info("Shutting down Echo…")
        for attr in ("peer_listener", "share_observer"):
            obj = getattr(self.adapter, attr, None)
            if obj is not None:
                try:
                    obj.stop()
                except Exception as e:
                    logger.error(f"Error stopping {attr}: {e}")
        try:
            self.adapter.core.server.close()
        except Exception as e:
            logger.error(f"Error closing server connection: {e}")
        event.accept()

    def _on_peers_changed(self,online_dict: dict):
        """
        Called every 5s when the heartbeat delivers. Diffs against the 
        existing QlistWidget in place to prevent flickering
        and maintain the current selection."""

        my_uname = self.adapter.core.settings.get("uname", "")

        existing_items = {}

        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            uname = item.data(Qt.UserRole)  # strip the "● " prefix
            existing_items[uname] = item

        for uname in online_dict:
            if uname == my_uname:
                continue  # skip self
            if uname in existing_items:
                item = existing_items[uname]  # still online, keep it
                item.setText(f"● {uname}(online)")  # refresh the text in case it changed
                item.setForeground(QColor("green"))
            else:
                new_item = QListWidgetItem(f"● {uname}(online)")
                new_item.setData(Qt.UserRole, uname)
                new_item.setForeground(QColor("green"))
                self.user_list.addItem(new_item)

        for uname,item in existing_items.items():
            if uname not in online_dict:
                item.setText(f"○ {uname}(offline)")
                item.setForeground(QColor("gray"))

        if self.selected_peer:
            is_online = self.selected_peer in online_dict
            self._update_gated_controls(is_online)

    def _on_user_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """
        Called when the user selects a different peer in the left pane.
        Updates the center and right panes accordingly.
        """
        if current is None:
            self.selected_peer = None
            self._disable_all_peer_controls()
            return

        uname = current.data(Qt.UserRole)
        self.selected_peer = uname
        is_online = uname in self.adapter.core.online_peers

        #update chat header and controls
        logger.info(f"Selected peer changed to: {uname} (online: {is_online})")
        self.lbl_chat_header.setText(f"<b>Chat with {uname}</b>" if is_online else f"<b>Chat with {uname} (offline)</b>")
        # is_online = uname in self.adapter.core.peers
        self._update_gated_controls(is_online)

        self._render_chat_history(uname)
        #async browse the files if online
        if is_online:
            self.lbl_files_header.setText(f"<b>{uname}'s Shared Files</b> (loading...)")
            self.file_tree.clear()
            self.adapter.browse_async(uname,on_success = self._on_browse_success, on_error = self._on_browse_error)
        else:
            self.lbl_files_header.setText(f"<b>{uname}'s Shared Files</b> (offline)")
            self.file_tree.clear()

    def _update_gated_controls(self, is_online: bool):
        """Enable or disable controls that depend on the selected peer's online status."""
        self.chat_input.setEnabled(is_online)
        self.btn_send_chat.setEnabled(is_online)
        #self.btn_download.setEnabled(is_online)
        #self.btn_file_info.setEnabled(is_online)
        self.btn_refresh_tree.setEnabled(is_online)
        self.chat_input.setPlaceholderText("Type a message..." if is_online else f"{self.selected_peer} is offline")

    def _disable_all_peer_controls(self):
        """Disable all controls that depend on a selected peer."""
        self.chat_input.setEnabled(False)
        self.btn_send_chat.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.btn_file_info.setEnabled(False)
        self.btn_refresh_tree.setEnabled(False)
        self.lbl_chat_header.setText("<i>Select a user to chat</i>")
        #self.lbl_files_header.setText("<b>Shared Files</b>")
        self.chat_display.clear()
        self.file_tree.clear()

    def _on_connection_lost(self, reason: str):
        server_ip = self.adapter.core.settings.get("server_ip", "unknown")
        logger.warning(f"Connection to server {server_ip} lost: {reason}")
        self.lbl_status.setText("● Disconnected")
        self.lbl_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.btn_reconnect.setEnabled(True)
        self.btn_search.setEnabled(False)

        #mark all listed users as offline
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            uname = item.data(Qt.UserRole)
            item.setText(f"○ {uname}(offline)")
            item.setForeground(QColor("gray"))
        if self.selected_peer:
            self._update_gated_controls(is_online=False)

    def _on_connect_done(self, success: bool, message: str):
        server_ip = self.adapter.core.settings.get("server_ip", "unknown")
        if success:
            self.lbl_status.setText(f"● Connected to {server_ip}")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
            self.btn_reconnect.setEnabled(False)
            self.btn_search.setEnabled(True)
            logger.info(f"Successfully connected and registered with server {server_ip}.")
        else:
            self._on_connection_lost(message)
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog(message, parent=self).exec_()


    