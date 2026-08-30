import logging
import time
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QTextEdit, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLabel, QFrame,
    QScrollArea, QDesktopWidget, QListWidgetItem,QMessageBox
)
from PyQt5.QtGui import QColor
from client.ui.file_progress_widget import FileProgressWidget
from utils.helpers import convert_size
from utils.types import TransferStatus
from typing import TYPE_CHECKING
from client import transfers
logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from client.adapter import CoreAdapter

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
    def __init__(self,adapter: "CoreAdapter"):
        super().__init__()
        self.adapter = adapter
        self.selected_peer = None
        self._last_seen: dict = {}  # uname -> last epoch seen online (widget-owned memory, 5.3.1)
        self.init_ui()
        self._wire_stub_actions()
        self._connect_adapter_signals()
        self._maybe_auto_connect()
        self.user_list.currentItemChanged.connect(self._on_user_selection_changed)
        self.file_tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.btn_refresh_tree.clicked.connect(self._refresh_current_tree)
        self.btn_file_info.clicked.connect(self._open_file_info)
        self._progress_widgets: dict[str, FileProgressWidget] = {}  # transfer_key -> widget
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(2000)  # 2-second quiet period
        self._rescan_timer.timeout.connect(self._trigger_auto_republish)

        # 5.3.8: watchdog Observer (in the adapter) -> share_changed -> restart the
        # debounce timer -> one republish. This is the detection source that was
        # missing; the timer alone had nothing to fire it.
        self.adapter.share_changed.connect(self.trigger_share_rescan)
        self.adapter.start_share_watch()

        # Session 6: without the inbound peer server running, no chat / file /
        # transfer request can ever reach us.

        self.adapter.start_peer_listener()
        self.adapter.core.on_direct_transfer_request = self._prompt_direct_transfer_consent
        self._restore_journal_transfers()  # 5.4.5: load any in-progress transfers from the journal


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
            #self.btn_search: "Search network",
            self.btn_settings: "Open settings",
            #self.btn_download: "Download selected",
            #self.btn_file_info: "Show file info",
            #self.btn_refresh_tree: "Refresh tree",
            #self.btn_send_chat: "Send chat",
        }
        for btn, label in stubs.items():
            btn.clicked.connect(lambda _=False, l=label: logger.info(f"[stub] {l} clicked"))
        self.btn_reconnect.clicked.connect(self._begin_connect)
        self.btn_send_chat.clicked.connect(self._send_chat_message)
        self.chat_input.returnPressed.connect(self._send_chat_message)
        self.chat_input.textChanged.connect(self._on_chat_input_changed)
        self.btn_search.clicked.connect(self._open_search_dialog)
        self.btn_download.clicked.connect(self._start_download_selected)

    def _connect_adapter_signals(self):
        """Bind every adapter signal to its slot — the one crossing from
        background threads into the GUI thread."""
        self.adapter.peers_changed.connect(self._on_peers_changed)
        self.adapter.connection_lost.connect(self._on_connection_lost)
        self.adapter.message_received.connect(self._on_message_received)
        self.adapter.notification.connect(self._on_notification)
        self.adapter.transfer_progress.connect(self._on_transfer_progress)


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
        self.adapter.stop_share_watch()  # stop + join the watchdog Observer
        listener = getattr(self.adapter, "peer_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception as e:
                logger.error(f"Error stopping peer listener: {e}")
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
        self._last_seen.update(online_dict)  # remember when we last saw each peer online

        existing_items = {}
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            existing_items[item.data(Qt.UserRole)] = item

        # online peers: update in place or append (never touch self)
        for uname in online_dict:
            if uname == my_uname:
                continue
            item = existing_items.get(uname)
            if item is None:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, uname)
                self.user_list.addItem(item)
            item.setText(f"● {uname} (online)")
            item.setForeground(QColor("green"))

        # anyone still listed but no longer online -> grey with last-active
        for uname, item in existing_items.items():
            if uname not in online_dict:
                self._mark_item_offline(item, uname)

        if self.selected_peer:
            self._update_gated_controls(self.selected_peer in online_dict)

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

        # grey every listed user (the whole server went away)
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            self._mark_item_offline(item, item.data(Qt.UserRole))
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

    # --- pane helpers --------------------------------------------------------

    def _mark_item_offline(self, item: QListWidgetItem, uname: str):
        item.setText(f"○ {uname} — last active {self._format_last_seen(self._last_seen.get(uname))}")
        item.setForeground(QColor("gray"))

    def _format_last_seen(self, ts) -> str:
        if not ts:
            return "unknown"
        delta = time.time() - ts
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta // 60)}m ago"
        if delta < 86400:
            return f"{int(delta // 3600)}h ago"
        return f"{int(delta // 86400)}d ago"

    def _render_chat_history(self, uname: str):
        """Render the stored conversation with `uname` into the chat pane.
        Minimal plain-text version; 5.3.4 (Session 6) swaps in the HTML
        formatter (construct_message_html + LEADING/TRAILING_HTML)."""
        history = self.adapter.core.message_history.get(uname, [])
        self.chat_display.setPlainText(
            "\n".join(f"{m.get('sender', '?')}: {m.get('content', '')}" for m in history)
        )

    def _on_browse_success(self, tree):
        """Fill the file tree from the browsed DirData list. `tree` is None on a
        server failure, [] for a user with an empty share, else a list[DirData].
        5.3.3 (Session 5) refines columns/placeholders; this is the functional
        first cut, already stashing each DirData on its row."""
        self.file_tree.clear()
        peer = self.selected_peer or ""
        if tree is None:
            self.lbl_files_header.setText(f"<b>{peer}'s Shared Files</b> (unavailable)")
            return
        self.lbl_files_header.setText(f"<b>{peer}'s Shared Files</b>")
        if not tree:
            self.file_tree.addTopLevelItem(QTreeWidgetItem(["(nothing shared)", "", "", ""]))
            return
        for node in tree:
            self.file_tree.addTopLevelItem(self._build_tree_item(node))
        self.file_tree.expandToDepth(0)

    def _build_tree_item(self, node: dict) -> QTreeWidgetItem:
        """Recursively turn a DirData node into a tree row, stashing the raw
        DirData on column 0 (Qt.UserRole) so selection handlers read it back
        without a re-walk (5.3.3's key trick)."""
        is_dir = node.get("type") == "directory"
        name = node.get("name", "?") + ("/" if is_dir else "")
        size = "" if is_dir else convert_size(node.get("size") or 0)
        kind = "dir" if is_dir else "file"
        file_hash = "" if is_dir else (node.get("hash") or "unverified")
        item = QTreeWidgetItem([name, size, kind, file_hash])
        item.setData(0, Qt.UserRole, node)
        for child in (node.get("children") or []):
            item.addChild(self._build_tree_item(child))
        return item

    def _on_browse_error(self, err: str):
        """Browse worker hit a transport error. Surface it on the file header."""
        logger.error(f"Browse failed for '{self.selected_peer}': {err}")
        self.file_tree.clear()
        self.lbl_files_header.setText(f"<b>{self.selected_peer or ''}'s Shared Files</b> (error)")

    def _on_tree_selection_changed(self):
        """Called when the user selects an item in the file tree."""
        selected_items = self.file_tree.selectedItems()
        has_valid_item = False
        if selected_items:
            data = selected_items[0].data(0, Qt.UserRole)
            has_valid_item = data is not None

        # Enable download button if at least one item is selected
        self.btn_download.setEnabled(has_valid_item)

        # Enable file info button only if exactly one item is selected
        self.btn_file_info.setEnabled(has_valid_item and len(selected_items) == 1)

    def _refresh_current_tree(self):
        """Refresh the file tree for the currently selected peer."""
        if self.selected_peer and self.selected_peer in self.adapter.core.online_peers:
            self.lbl_files_header.setText(f"<b>{self.selected_peer}'s Shared Files</b> (refreshing...)")
            #self.file_tree.clear()
            self.adapter.browse_async(self.selected_peer, on_success=self._on_browse_success, on_error=self._on_browse_error)

    def _open_file_info(self):
        """Open the file info dialog for the currently selected item in the file tree."""
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            logger.warning("File info requested but no single item is selected.")
            return
        data = selected_items[0].data(0, Qt.UserRole)
        if data:
            from client.ui.file_info_dialog import FileInfoDialog
            dialog = FileInfoDialog(data, parent=self)
            dialog.exec_()

    def trigger_share_rescan(self):
        """ Restarts the 2s debounce timer on file events """
        self._rescan_timer.start()

    def _trigger_auto_republish(self):
        """ Publishes the updated share tree off the GUI thread """
        logger.info("Auto-rescan debounce expired. Publishing updated share data to server...")
        self.adapter.publish_share_async(
            on_success=lambda: logger.info("Share data auto-republished successfully.")
        )

    def _send_chat_message(self):
        """Send the message in the input box to the selected peer."""
        if not self.selected_peer:
            logger.warning("Attempted to send chat message with no peer selected.")
            return
        message = self.chat_input.text().strip()
        if not message:
            return  # Don't send empty messages

        if len(message.encode('utf-8'))>256:
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog("Message exceeds 256 bytes. Please shorten your message.", parent=self).exec_()
            return
        self.chat_input.clear()

        my_uname = self.adapter.core.settings.get("uname", "You")
        msg_obj = {"sender": my_uname, "content": message}

        from utils.helpers import construct_message_html
        self.chat_display.append(construct_message_html(msg_obj, is_self=True))
        self._scroll_chat_to_bottom()
        
        self.adapter.send_chat_async(
            self.selected_peer,
            message,
            on_success=None,
            on_error=lambda err: logger.error(f"Failed to send message to {self.selected_peer}: {err}")
        )

    def _on_chat_input_changed(self, text: str):
        """Enable or disable the send button based on whether there's text to send."""
        byte_len = len(text.encode('utf-8'))
        is_valid = 0 < byte_len <= 256
        is_online = self.selected_peer and self.selected_peer in self.adapter.core.online_peers 
        self.btn_send_chat.setEnabled(bool(text.strip()) and is_valid and is_online)

    

    def _on_message_received(self, msg: dict):
        """Inbound P2P chat message (GUI thread). Append it in-app if we're
        looking at that conversation; either way it's already in
        core.message_history, so switching to the sender re-renders it. The
        desktop popup is handled by _on_notification (the listener fires
        core.notify -> notification signal), so we DON'T notify here — that
        double-firing was the bug."""
        sender = msg.get("sender", "Unknown")
        if self.selected_peer == sender:
            from utils.helpers import construct_message_html
            self.chat_display.append(construct_message_html(msg, is_self=False))
            self._scroll_chat_to_bottom()

    def _on_notification(self, title: str, body: str):
        """Route a core notification to a DESKTOP popup ONLY when this window
        isn't the active one (5.3.7); when it's focused the in-window update is
        enough. show_notifications was already honored by core.notify()."""
        if self.isActiveWindow():
            return
        try:
            from notifypy import Notify
            n = Notify()
            n.application_name = "Echo"
            n.title = title
            n.message = body
            n.send(block=False)
        except Exception as e:
            logger.error(f"Desktop notification failed: {e}")

    def _render_chat_history(self, uname: str):
        """ Render full conversation history using rich HTML formatting """
        self.chat_display.clear()
        history = self.adapter.core.message_history.get(uname, [])
        my_uname = self.adapter.core.settings.get("uname", "")

        from utils.helpers import construct_message_html
        html_blocks = []
        for m in history:
            is_self = m.get("sender") == my_uname or m.get("sender") == "You"
            html_blocks.append(construct_message_html(m, is_self=is_self))

        self.chat_display.setHtml("".join(html_blocks))
        self._scroll_chat_to_bottom()

    def _scroll_chat_to_bottom(self):
        """ Automatically scrolls the chat pane to the newest message """
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _open_search_dialog(self):
        """ Opens the global search modal """
        from client.ui.file_search_dialog import FileSearchDialog
        dlg = FileSearchDialog(
            self.adapter, 
            on_go_to_owner=self._select_peer_by_name, 
            parent=self
        )
        dlg.exec_()

    def _select_peer_by_name(self, uname: str):
        """
        Selects uname in the user list and triggers a browse.
        Called when 'Go to Owner' is triggered from the search modal.
        """
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            if item.data(Qt.UserRole) == uname:
                self.user_list.setCurrentItem(item)
                logger.info(f"Selected peer '{uname}' via Go-To-Owner.")
                return
        logger.warning(f"Peer '{uname}' not currently found in visible peer list.")

    def _start_download_selected(self):
        """Initiates a download for the currently selected file/folder in the tree.

        A file gets one progress row + one worker. A folder is FLATTENED into its
        constituent files, each getting its own row + worker keyed by that file's
        share-relative path. This is the 5.3.5 fix: download_folder streamed each
        file under its own per-file key, so a single folder-keyed row never
        received any progress tick — now every file has a row whose key matches."""
        selected_items = self.file_tree.selectedItems()
        if not selected_items or not self.selected_peer:
            logger.warning("Download requested but no item is selected or no peer is selected.")
            return
        data = selected_items[0].data(0, Qt.UserRole)
        owner = self.selected_peer

        if data.get("type") == "directory":
            self._start_folder_download(owner, data)
        else:
            self._start_file_download(owner, data)

    def _start_file_download(self, owner: str, file_data: dict,
                             dest_subpath: str = None, expected_hash: str = None):
        """Create one progress row and spawn one download_file worker for a single
        file. `dest_subpath` (set for folder members) mirrors the source tree under
        Downloads; `expected_hash` skips the hash round-trip when we already have it."""
        remote_path = file_data.get("path", "")
        size = file_data.get("size") or 0
        key = transfers._transfer_key(owner, remote_path)
        if key in self._progress_widgets:
            logger.info(f"Transfer '{key}' already active; ignoring duplicate request.")
            return
        self._create_progress_widget(key, file_data.get("name") or remote_path, size)
        self.adapter.run_async(
            transfers.download_file,
            lambda ok: self._on_download_complete(key, ok),
            lambda err: self._on_download_error(key, err),
            self.adapter.core, owner, remote_path, size, 0, expected_hash, dest_subpath,
        )

    def _start_folder_download(self, owner: str, folder_data: dict):
        """Flatten a selected folder into its files and start each as its own
        transfer (per-file row + worker), mirroring the tree under Downloads."""
        from utils.helpers import get_files_in_dir
        files: list = []
        get_files_in_dir(folder_data.get("children") or [], files)
        if not files:
            logger.info(f"Folder '{folder_data.get('name')}' has no files to download.")
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog(f"'{folder_data.get('name')}' contains no files to download.", parent=self).exec_()
            return
        logger.info(f"Downloading folder '{folder_data.get('name')}' ({len(files)} files) from {owner}.")
        for f in files:
            self._start_file_download(
                owner, f, dest_subpath=f.get("path"), expected_hash=f.get("hash")
            )

    def _create_progress_widget(self, key: str, filename: str, total_size: int, initial_status = TransferStatus.DOWNLOADING):
        if key in self._progress_widgets:
            logger.warning(f"Progress widget for {key} already exists. Skipping creation.")
            return self._progress_widgets[key]

        from client.ui.file_progress_widget import FileProgressWidget
        widget = FileProgressWidget(key,filename, total_size, initial_status = initial_status)
        widget.pause_requested.connect(self._pause_download)
        widget.resume_requested.connect(self._resume_download)

        self.transfers_layout.insertWidget(0,widget)
        self._progress_widgets[key] = widget
        return widget

    def _on_transfer_progress(self, key: str,progress_data: dict):
        """
        Called every 16kb chunk tick on the GUI thread. Updates the corresponding progress widget.
        """

        widget = self._progress_widgets.get(key)
        if not widget:
            logger.warning(f"Received progress update for unknown transfer key: {key}")
            return

        widget.update_progress(progress_data)

    def _pause_download(self, key: str):
        logger.info(f"Pause requested for transfer {key}")
        self.adapter.core.pause_transfer(key)

    def _resume_download(self, key: str):
        logger.info(f"Resume requested for transfer {key}")
        owner, remote_path = key.split(":", 1)

        # Prefer the journal entry: it preserves the fields the in-memory record
        # drops — the file hash and the dest_subpath needed to re-mirror a folder
        # member back to the right place under Downloads. Fall back to the live
        # transfer record (e.g. a pause within this same session, pre-journal-flush).
        entry = self.adapter.core.get_journal_entry(key)
        rec = self.adapter.core.get_transfer(key)
        total_size = (entry or rec or {}).get("total", 0)
        expected_hash = entry.get("hash") if entry else None
        dest_subpath = entry.get("dest_subpath") if entry else None

        meta = {"path": remote_path, "size": total_size, "hash": expected_hash}

        self.adapter.run_async(
            transfers.resume_download,
            lambda ok: self._on_download_complete(key, ok),
            lambda err: self._on_download_error(key, err),
            self.adapter.core, owner, meta, dest_subpath,
        )

    def _on_download_complete(self, key: str, success: bool):
        """Worker returned. success=True -> mark the row done and retire it after a
        beat. success=False is ambiguous (download_file returns False on BOTH a
        user pause and a genuine failure), so consult the transfer status to tell
        them apart: a paused row stays put for resume, a failed one is marked ✗."""
        widget = self._progress_widgets.get(key)
        if success:
            logger.info(f"Download completed successfully for {key}")
            if widget:
                widget.update_progress({
                    "progress": widget.total_size, "total": widget.total_size,
                    "status": TransferStatus.COMPLETED,
                })
            # Leave the ✓ visible briefly, then retire the row.
            QTimer.singleShot(4000, lambda: self._remove_progress_widget(key))
            return

        rec = self.adapter.core.get_transfer(key)
        status = rec.get("status") if rec else None
        if status == TransferStatus.PAUSED:
            logger.info(f"Download {key} paused; row kept for resume.")
            if widget:
                widget.update_progress({
                    "progress": rec.get("progress", 0),
                    "total": rec.get("total", widget.total_size),
                    "status": TransferStatus.PAUSED,
                })
        else:
            logger.error(f"Download failed for {key}")
            if widget:
                widget.update_progress({
                    "progress": rec.get("progress", 0) if rec else 0,
                    "total": widget.total_size, "status": TransferStatus.FAILED,
                })

    def _on_download_error(self, key: str, err_msg: str):
        """The worker raised (unexpected — download_file catches its own transport
        errors and returns False). Mark the row failed; if somehow there's no row,
        fall back to a dialog so the error isn't swallowed silently."""
        logger.error(f"Download worker error for {key}: {err_msg}")
        widget = self._progress_widgets.get(key)
        if widget:
            widget.update_progress({
                "progress": 0, "total": widget.total_size, "status": TransferStatus.FAILED,
            })
        else:
            from client.ui.error_dialog import ErrorDialog
            ErrorDialog(f"Download error: {err_msg}", parent=self).exec_()

    def _remove_progress_widget(self, key: str):
        """Retire a finished transfer's row from the pane."""
        widget = self._progress_widgets.pop(key, None)
        if widget is not None:
            self.transfers_layout.removeWidget(widget)
            widget.deleteLater()
    def _restore_journal_transfers(self):
        """ Pre-creates PAUSED rows for any incomplete downloads found in the journal """
        resumable = self.adapter.core.get_resumable_transfers()
        for entry in resumable:
            owner = entry.get("uname", "unknown")
            filepath = entry.get("filepath", "")
            key = f"{owner}:{filepath}"
            size = entry.get("total", 0)
            received = entry.get("received", 0)
            
            w = self._create_progress_widget(key, filepath, size, initial_status=TransferStatus.PAUSED)
            w.update_progress({"progress": received, "total": size, "status": TransferStatus.PAUSED})

    

    def _prompt_direct_transfer_consent(self, metadata: dict) -> bool:
        """
        Called when an inbound push request arrives.
        Pops an interactive modal asking the user to Accept or Decline.
        """
        sender = metadata.get("sender") or "A peer"
        filename = metadata.get("path", "file")
        size = metadata.get("size", 0)

        # Use convert_size for readable file size
        from utils.helpers import convert_size
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Incoming File Transfer")
        msg_box.setText(f"<b>{sender}</b> wants to send you a file:")
        msg_box.setInformativeText(f"📄 <b>{filename}</b> ({convert_size(size)})\n\nDo you want to accept this transfer?")
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.Yes)
        
        reply = msg_box.exec_()
        accepted = (reply == QMessageBox.Yes)

        if accepted:
            logger.info(f"User accepted direct transfer of '{filename}' from {sender}.")
            # Create a non-pausable progress widget for the incoming stream
            from client import transfers
            key = transfers._transfer_key(sender, filename)
            self._create_progress_widget(key, filename, size, allow_pause=False)
        else:
            logger.info(f"User declined direct transfer of '{filename}' from {sender}.")

        return accepted