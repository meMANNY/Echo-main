import html
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QTextEdit, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLabel, QFrame,
    QScrollArea, QDesktopWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QAbstractItemView,
)

from client.ui.error_dialog import ErrorDialog
from client.ui.file_progress_widget import FileProgressWidget
from utils.constants import MESSAGE_MAX_LEN
from utils.helpers import convert_size, construct_message_html, get_files_in_dir
from utils.types import TransferStatus

if TYPE_CHECKING:
    from client.adapter import CoreAdapter

logger = logging.getLogger(__name__)

# 5.3.8: a burst of watcher events collapses into ONE republish this long after
# the last event.
_REPUBLISH_QUIET_MS = 2000
# How long finished / failed rows linger in Active Transfers before retiring.
_RETIRE_DONE_MS = 4000
_RETIRE_FAILED_MS = 15000

_TERMINAL = (TransferStatus.COMPLETED, TransferStatus.FAILED)

# ┌────────────────────────────────────────────────────────────────────────────────────────┐
# │  👤 alice  │  ● Connected (127.0.0.1)  │  [Reconnect]     [🔍 Search]   [⚙ Settings]  │
# ├─────────────────────┬────────────────────────────────────┬─────────────────────────────┤
# │ 👥 USERS            │ 💬 CHAT (with 'bob')               │ 📁 FILES & TRANSFERS        │
# │  ● bob              │ bob: Hi Alice!                     │ [Tree: bob's files]         │
# │  ○ charlie (2m ago) │ You: Hey Bob!                      │ [Download][Info][Refresh].. │
# │                     │                                    │ Your share: [Import..][Share]│
# │ [📂 My Share]       │ [Type message...    ] 12/256 [Send]│ ⬇ ACTIVE TRANSFERS          │
# └─────────────────────┴────────────────────────────────────┴─────────────────────────────┘
#
# Thread rule: every method here runs on the GUI thread. Background work goes
# through the adapter (pool workers) and comes back as queued signals/callbacks.
# This window never imports client.core or client.transfers — the adapter is
# its only touchpoint (5.1.2c).


class EchoMainWindow(QMainWindow):
    def __init__(self, adapter: "CoreAdapter"):
        super().__init__()
        self.adapter = adapter

        # --- UI state (widget-layer memory; never stored in core) ---
        self.selected_peer: Optional[str] = None   # whose chat is open
        self._tree_owner: Optional[str] = None     # whose share the tree shows
        self._online: set = set()                  # peers online at the last heartbeat
        self._last_seen: dict = {}                 # uname -> LOCAL time last seen online (5.3.1)
        self._unread: dict = {}                    # uname -> unread inbound messages
        self._connected = False
        self._connecting = False
        self._share_dirty = False                  # a share change happened while offline
        self._progress_widgets: dict = {}          # transfer key -> FileProgressWidget
        self._groups: dict = {}                    # folder aggregate rows (5.3.5)
        self._member_group: dict = {}              # member transfer key -> group key

        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(_REPUBLISH_QUIET_MS)
        self._rescan_timer.timeout.connect(self._trigger_auto_republish)

        self.init_ui()
        self._wire_actions()
        self._connect_adapter_signals()

        # Background owners: inbound peer server (no chat/files arrive without
        # it) and the share watcher (5.3.8).
        if not self.adapter.start_peer_listener():
            QTimer.singleShot(0, self._warn_listener_failed)
        self.adapter.start_share_watch()

        self._restore_journal_transfers()  # interrupted downloads come back PAUSED

        # Returning-user flow (5.1.3): never block the first paint on the network.
        if self.adapter.connected:
            self._set_connection_state("connected")
        else:
            self._begin_connect()
        self._refresh_controls()

    # =========================================================================
    # Layout
    # =========================================================================

    def init_ui(self):
        self.setWindowTitle(f"Echo — P2P File Sharing & Chat ({self.adapter.my_uname})")
        self.resize(1100, 700)
        self.setMinimumSize(900, 550)

        root_widget = QWidget()
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._create_top_bar())

        panes_layout = QHBoxLayout()
        panes_layout.setSpacing(10)
        self.left_pane = self._create_left_pane()
        panes_layout.addWidget(self.left_pane, stretch=2)
        self.center_pane = self._create_center_pane()
        panes_layout.addWidget(self.center_pane, stretch=4)
        self.right_pane = self._create_right_pane()
        panes_layout.addWidget(self.right_pane, stretch=4)
        root_layout.addLayout(panes_layout)

        self.setCentralWidget(root_widget)
        self.statusBar().showMessage("Starting…")
        self.center_on_screen()

    def center_on_screen(self):
        frame_geometry = self.frameGeometry()
        center_point = QDesktopWidget().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def _create_top_bar(self):
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        self.lbl_user_info = QLabel(f"👤 Logged in as: <b>{html.escape(self.adapter.my_uname)}</b>")
        layout.addWidget(self.lbl_user_info)
        layout.addSpacing(20)
        self.lbl_status = QLabel("")
        layout.addWidget(self.lbl_status)
        self.btn_reconnect = QPushButton("Reconnect")
        self.btn_reconnect.setEnabled(False)  # enabled only while disconnected
        layout.addWidget(self.btn_reconnect)
        layout.addStretch()
        self.btn_search = QPushButton("🔍 Search Network")
        layout.addWidget(self.btn_search)
        self.btn_settings = QPushButton("⚙ Settings")
        layout.addWidget(self.btn_settings)
        return bar

    def _create_left_pane(self):
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("<b>Network Peers</b>"))
        self.user_list = QListWidget()
        layout.addWidget(self.user_list)
        self.btn_my_share = QPushButton("📂 My Share")
        self.btn_my_share.setToolTip("Show the files you are sharing, as the server sees them")
        layout.addWidget(self.btn_my_share)
        return pane

    def _create_center_pane(self):
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)
        self.lbl_chat_header = QLabel("<i>Select a user to chat</i>")
        layout.addWidget(self.lbl_chat_header)
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display)

        h_input = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Select a user to chat")
        self.chat_input.setEnabled(False)
        # MESSAGE_MAX_LEN is in BYTES (UTF-8), so the counter counts bytes —
        # an emoji costs 4. Over-length input is blocked at the box (5.3.4).
        self.lbl_char_count = QLabel(f"0/{MESSAGE_MAX_LEN}")
        self.lbl_char_count.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        self.lbl_char_count.setToolTip("Message size in bytes (UTF-8)")
        self.btn_send_chat = QPushButton("Send")
        self.btn_send_chat.setEnabled(False)
        h_input.addWidget(self.chat_input)
        h_input.addWidget(self.lbl_char_count)
        h_input.addWidget(self.btn_send_chat)
        layout.addLayout(h_input)
        return pane

    def _create_right_pane(self) -> QWidget:
        pane = QFrame()
        pane.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(6, 6, 6, 6)

        self.lbl_files_header = QLabel("<b>Shared Files</b>")
        layout.addWidget(self.lbl_files_header)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Name", "Size", "Type", "Hash"])
        self.file_tree.setColumnWidth(0, 180)
        self.file_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.file_tree)

        h_buttons = QHBoxLayout()
        self.btn_download = QPushButton("Download")
        self.btn_file_info = QPushButton("File Info")
        self.btn_refresh_tree = QPushButton("Refresh")
        self.btn_send_file_direct = QPushButton("Send File Directly")
        for b in (self.btn_download, self.btn_file_info, self.btn_refresh_tree, self.btn_send_file_direct):
            b.setEnabled(False)
            h_buttons.addWidget(b)
        layout.addLayout(h_buttons)

        # Your own share (5.2.3 / 5.3.8 manual companion). Import just copies
        # into the share folder — the watcher picks the change up and the
        # debounce republishes. Share Now is the "don't wait" escape hatch.
        h_share = QHBoxLayout()
        h_share.addWidget(QLabel("Your share:"))
        self.btn_import_files = QPushButton("Import Files…")
        self.btn_import_folder = QPushButton("Import Folder…")
        self.btn_share_now = QPushButton("Share Now")
        self.btn_share_now.setToolTip("Republish your share folder to the server immediately")
        h_share.addWidget(self.btn_import_files)
        h_share.addWidget(self.btn_import_folder)
        h_share.addStretch()
        h_share.addWidget(self.btn_share_now)
        layout.addLayout(h_share)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)

        layout.addWidget(QLabel("<b>Active Transfers</b>"))
        self.transfers_scroll = QScrollArea()
        self.transfers_scroll.setWidgetResizable(True)
        self.transfers_scroll.setFixedHeight(160)
        self.transfers_container = QWidget()
        self.transfers_layout = QVBoxLayout(self.transfers_container)
        self.transfers_layout.setContentsMargins(4, 4, 4, 4)
        self.transfers_layout.setSpacing(4)
        self.transfers_layout.addStretch()  # pushes progress rows to the top
        self.transfers_scroll.setWidget(self.transfers_container)
        layout.addWidget(self.transfers_scroll)
        return pane

    # =========================================================================
    # Wiring
    # =========================================================================

    def _wire_actions(self):
        self.btn_reconnect.clicked.connect(self._begin_connect)
        self.btn_search.clicked.connect(self._open_search_dialog)
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        self.btn_my_share.clicked.connect(self._view_my_share)

        self.user_list.currentItemChanged.connect(self._on_user_selection_changed)
        self.chat_input.textChanged.connect(self._on_chat_input_changed)
        self.chat_input.returnPressed.connect(self._send_chat_message)
        self.btn_send_chat.clicked.connect(self._send_chat_message)

        self.file_tree.itemSelectionChanged.connect(self._refresh_controls)
        self.btn_download.clicked.connect(self._download_selected)
        self.btn_file_info.clicked.connect(self._open_file_info)
        self.btn_refresh_tree.clicked.connect(self._refresh_current_tree)
        self.btn_send_file_direct.clicked.connect(self._send_file_directly)

        self.btn_import_files.clicked.connect(self._import_files)
        self.btn_import_folder.clicked.connect(self._import_folder)
        self.btn_share_now.clicked.connect(self._share_now)

    def _connect_adapter_signals(self):
        """Bind every adapter signal to its slot — the one crossing from
        background threads into the GUI thread."""
        self.adapter.peers_changed.connect(self._on_peers_changed)
        self.adapter.connection_lost.connect(self._on_connection_lost)
        self.adapter.message_received.connect(self._on_message_received)
        self.adapter.notification.connect(self._on_notification)
        self.adapter.transfer_progress.connect(self._on_transfer_progress)
        self.adapter.transfer_request.connect(self._prompt_direct_transfer_consent)
        self.adapter.share_changed.connect(self._on_share_changed)
        self.adapter.share_republished.connect(self._on_share_republished)

    # =========================================================================
    # Capability gating — the UI state machine (5.3.2, concept 7)
    # =========================================================================

    def _selected_tree_data(self) -> list:
        """DirData of every selected real row (placeholder rows carry none)."""
        out = []
        for item in self.file_tree.selectedItems():
            data = item.data(0, Qt.UserRole)
            if data is not None:
                out.append(data)
        return out

    def _refresh_controls(self):
        """Derive EVERY button's enabled state from the current facts, in one
        place, so no control is ever clickable when its preconditions don't hold."""
        connected = self._connected
        me = self.adapter.my_uname
        peer = self.selected_peer
        peer_online = connected and self.adapter.is_online(peer)
        owner = self._tree_owner
        viewing_own = owner is not None and owner == me
        owner_reachable = connected and owner is not None and (viewing_own or self.adapter.is_online(owner))
        selected = self._selected_tree_data()

        text = self.chat_input.text()
        nbytes = len(text.encode("utf-8"))
        self.chat_input.setEnabled(peer_online)
        self.btn_send_chat.setEnabled(peer_online and bool(text.strip()) and nbytes <= MESSAGE_MAX_LEN)
        self.btn_send_file_direct.setEnabled(peer_online)

        self.btn_refresh_tree.setEnabled(owner_reachable)
        self.btn_download.setEnabled(owner_reachable and not viewing_own and bool(selected))
        self.btn_file_info.setEnabled(len(selected) == 1)

        self.btn_search.setEnabled(connected)
        self.btn_my_share.setEnabled(connected)
        self.btn_share_now.setEnabled(connected)
        self.btn_reconnect.setEnabled(not connected and not self._connecting)

        if not peer:
            placeholder = "Select a user to chat"
        elif not connected:
            placeholder = "Not connected to the server"
        elif not peer_online:
            placeholder = f"{peer} is offline"
        else:
            placeholder = "Type a message..."
        self.chat_input.setPlaceholderText(placeholder)

    # =========================================================================
    # Connection state (5.3.6)
    # =========================================================================

    def _set_connection_state(self, state: str):
        server_ip = self.adapter.settings.get("server_ip", "?")
        self._connected = state == "connected"
        self._connecting = state == "connecting"
        if state == "connected":
            self.lbl_status.setText(f"● Connected to {server_ip}")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
        elif state == "connecting":
            self.lbl_status.setText(f"● Connecting to {server_ip}…")
            self.lbl_status.setStyleSheet("color: #d35400; font-weight: bold;")
        else:
            self.lbl_status.setText("● Disconnected")
            self.lbl_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self._refresh_controls()

    def _begin_connect(self):
        if self._connecting:
            return
        s = self.adapter.settings
        self._set_connection_state("connecting")
        self.statusBar().showMessage("Connecting to the server…")
        self.adapter.connect_and_register_async(
            self.adapter.my_uname, s.get("server_ip", ""), self._on_connect_done
        )

    def _on_connect_done(self, success: bool, message: str, reason: str):
        if success:
            self._set_connection_state("connected")
            self._share_dirty = False  # the connect flow just published the share
            self.statusBar().showMessage("Connected and registered.", 4000)
            logger.info("Connected and registered with the server.")
            if self._tree_owner == self.adapter.my_uname:
                self._refresh_current_tree()
            return

        self._set_connection_state("disconnected")
        self._grey_all_users()
        self.statusBar().showMessage("Not connected. Fix the problem, then press Reconnect.")
        # User-initiated (launch / Reconnect / settings change), so one dialog
        # is right here; the heartbeat path never pops dialogs.
        ErrorDialog(
            message, title="Couldn't connect",
            on_open_settings=None if reason == "user_exists" else self._open_settings_dialog,
            parent=self,
        ).exec_()

    def _on_connection_lost(self, reason: str):
        """The server socket tore down. Coalesced into ONE calm state change —
        banner + disabled controls + Reconnect — never a dialog (concept 9)."""
        if self._connecting:
            return  # the in-flight connect attempt reports its own outcome
        if not self._connected:
            return
        logger.warning(f"Connection to server lost: {reason}")
        self._set_connection_state("disconnected")
        self._grey_all_users()
        self.statusBar().showMessage("Lost the connection to the server. Press Reconnect when it's back.")

    def _warn_listener_failed(self):
        ErrorDialog(
            "Echo couldn't open its incoming port, so chat messages and file "
            "requests from other users can't reach you. Is another copy of Echo "
            "already running on this computer?",
            title="Incoming connections disabled", parent=self,
        ).exec_()

    # =========================================================================
    # Users (5.3.1) and selection (5.3.2)
    # =========================================================================

    def _find_user_item(self, uname: str) -> Optional[QListWidgetItem]:
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            if item.data(Qt.UserRole) == uname:
                return item
        return None

    def _render_user_item(self, item: QListWidgetItem, uname: str):
        unread = self._unread.get(uname, 0)
        badge = f"  ({unread} new)" if unread else ""
        if uname in self._online:
            item.setText(f"● {uname}{badge}")
            item.setForeground(QColor("#27ae60"))
        else:
            item.setText(f"○ {uname} — last active {self._format_last_seen(self._last_seen.get(uname))}{badge}")
            item.setForeground(QColor("gray"))
        font = item.font()
        font.setBold(bool(unread))
        item.setFont(font)

    def _on_peers_changed(self, online_dict: dict):
        """Heartbeat delivery (every HEARTBEAT_TIMER). DIFFS against the rendered
        list — update in place, append only new users — so the selection
        survives and nothing flickers (retained-mode reconciliation)."""
        me = self.adapter.my_uname
        now = time.time()
        self._online = {u for u in online_dict if u != me}
        for uname in self._online:
            # Local clock, not the server's timestamp: the two machines' clocks
            # needn't agree, and "last active Xm ago" is computed locally.
            self._last_seen[uname] = now
            if self._find_user_item(uname) is None:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, uname)
                self.user_list.addItem(item)
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            self._render_user_item(item, item.data(Qt.UserRole))
        self._update_chat_header()
        self._refresh_controls()

    def _grey_all_users(self):
        self._online = set()
        for i in range(self.user_list.count()):
            item = self.user_list.item(i)
            self._render_user_item(item, item.data(Qt.UserRole))
        self._update_chat_header()
        self._refresh_controls()

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

    def _update_chat_header(self):
        peer = self.selected_peer
        if not peer:
            self.lbl_chat_header.setText("<i>Select a user to chat</i>")
        elif self._connected and self.adapter.is_online(peer):
            self.lbl_chat_header.setText(f"<b>Chat with {html.escape(peer)}</b>")
        else:
            self.lbl_chat_header.setText(f"<b>Chat with {html.escape(peer)}</b> <i>(offline)</i>")

    def _on_user_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if current is None:
            self.selected_peer = None
            self.chat_display.clear()
            self._update_chat_header()
            if self._tree_owner != self.adapter.my_uname:
                self._tree_owner = None
                self.file_tree.clear()
                self.lbl_files_header.setText("<b>Shared Files</b>")
            self._refresh_controls()
            return

        uname = current.data(Qt.UserRole)
        self.selected_peer = uname
        if self._unread.pop(uname, None):
            self._render_user_item(current, uname)
        self._update_chat_header()
        self._render_chat_history(uname)
        self._show_tree_for(uname)
        self._refresh_controls()

    def _select_peer_by_name(self, uname: str):
        """Go-to-owner from search: select the user and browse them (4.4.2)."""
        if uname == self.adapter.my_uname:
            self._view_my_share()
            return
        item = self._find_user_item(uname)
        if item is None:
            self.statusBar().showMessage(f"{uname} isn't in your peer list right now.", 5000)
            return
        self.user_list.setCurrentItem(item)

    # =========================================================================
    # Share tree (5.3.3) + File Info (5.2.6)
    # =========================================================================

    def _files_label(self, owner: str) -> str:
        if owner == self.adapter.my_uname:
            return "<b>Your Shared Files</b>"
        return f"<b>{html.escape(owner)}'s Shared Files</b>"

    def _show_tree_for(self, owner: str):
        self._tree_owner = owner
        self.file_tree.clear()
        label = self._files_label(owner)
        if not self._connected:
            self.lbl_files_header.setText(f"{label} <i>(not connected)</i>")
        elif owner != self.adapter.my_uname and not self.adapter.is_online(owner):
            self.lbl_files_header.setText(f"{label} <i>(offline)</i>")
        else:
            self.lbl_files_header.setText(f"{label} <i>(loading…)</i>")
            self._browse(owner)
        self._refresh_controls()

    def _browse(self, owner: str):
        # Results are tagged with the owner they were requested for, so a slow
        # reply for a user you've since clicked away from is dropped, not drawn.
        self.adapter.browse_async(
            owner,
            on_success=lambda tree, o=owner: self._on_browse_result(o, tree),
            on_error=lambda err, o=owner: self._on_browse_error(o, err),
        )

    def _refresh_current_tree(self):
        owner = self._tree_owner
        if owner is None or not self._connected:
            return
        self.lbl_files_header.setText(f"{self._files_label(owner)} <i>(refreshing…)</i>")
        self._browse(owner)

    def _view_my_share(self):
        self.user_list.setCurrentRow(-1)  # deselect: chat pane goes idle
        self._show_tree_for(self.adapter.my_uname)

    def _placeholder_row(self, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text, "", "", ""])
        item.setFlags(Qt.NoItemFlags)  # not selectable, carries no DirData
        return item

    def _on_browse_result(self, owner: str, tree):
        """None = the browse failed; [] = nothing shared; else list[DirData].
        The 4.3 None-vs-[] distinction, kept visible in pixels."""
        if owner != self._tree_owner:
            return  # stale reply
        self.file_tree.clear()
        label = self._files_label(owner)
        if tree is None:
            self.lbl_files_header.setText(f"{label} <span style='color:#c0392b'>(couldn't load)</span>")
            self.file_tree.addTopLevelItem(self._placeholder_row("Couldn't load this share — try Refresh."))
        elif not tree:
            self.lbl_files_header.setText(label)
            self.file_tree.addTopLevelItem(self._placeholder_row("(nothing shared)"))
        else:
            self.lbl_files_header.setText(label)
            for node in tree:
                self.file_tree.addTopLevelItem(self._build_tree_item(node))
            self.file_tree.expandToDepth(0)
        self._refresh_controls()

    def _on_browse_error(self, owner: str, err: str):
        if owner != self._tree_owner:
            return
        logger.error(f"Browse failed for '{owner}': {err}")
        self._on_browse_result(owner, None)

    def _build_tree_item(self, node: dict) -> QTreeWidgetItem:
        """Recursively turn a DirData node into a row, stashing the raw DirData
        on column 0 (Qt.UserRole): the tree IS the model (5.3.3)."""
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

    def _open_file_info(self):
        selected = self._selected_tree_data()
        if len(selected) != 1:
            return
        from client.ui.file_info_dialog import FileInfoDialog
        FileInfoDialog(selected[0], parent=self).exec_()

    # =========================================================================
    # Own share: import, manual publish, auto-rescan (5.3.8)
    # =========================================================================

    def _on_share_changed(self):
        """Watcher event (any thread -> queued here). Restart the quiet-period
        timer; only the LAST event of a burst leads to a publish."""
        self._rescan_timer.start()

    def _trigger_auto_republish(self):
        if not self._connected:
            self._share_dirty = True  # the next successful connect publishes
            return
        logger.info("Share changed; republishing after the quiet period.")
        self.statusBar().showMessage("Updating share…")
        self.adapter.publish_share_async()

    def _share_now(self):
        self._rescan_timer.stop()  # this publish covers any pending change
        self.statusBar().showMessage("Updating share…")
        self.adapter.publish_share_async()

    def _on_share_republished(self, ok: bool):
        if ok:
            self.statusBar().showMessage("Share updated.", 4000)
            if self._tree_owner == self.adapter.my_uname:
                self._refresh_current_tree()
        else:
            self.statusBar().showMessage("Couldn't update your share on the server (not connected?).", 6000)

    def _import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import files into your share")
        if paths:
            self._import(paths)

    def _import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Import a folder into your share")
        if folder:
            self._import([folder])

    def _import(self, paths: list):
        self.statusBar().showMessage(f"Copying {len(paths)} item(s) into your share…")
        self.adapter.import_into_share_async(
            paths,
            on_success=self._on_import_done,
            on_error=lambda err: self._on_import_failed(err),
        )

    def _on_import_done(self, result):
        copied, skipped = result
        parts = []
        if copied:
            parts.append(f"Imported {len(copied)} item(s)")
        if skipped:
            parts.append(f"skipped {len(skipped)} (already in your share, or not a file/folder)")
        msg = "; ".join(parts) or "Nothing imported"
        if copied and self.adapter.watcher_active:
            msg += ". Your share will update in a moment."
        self.statusBar().showMessage(msg + ("" if msg.endswith(".") else "."), 6000)

    def _on_import_failed(self, err: str):
        self.statusBar().clearMessage()
        ErrorDialog(f"Import failed: {err}", parent=self).exec_()

    # =========================================================================
    # Chat (5.3.4) + notifications (5.3.7)
    # =========================================================================

    def _on_chat_input_changed(self, text: str):
        nbytes = len(text.encode("utf-8"))
        over = nbytes > MESSAGE_MAX_LEN
        self.lbl_char_count.setText(f"{nbytes}/{MESSAGE_MAX_LEN}")
        self.lbl_char_count.setStyleSheet(
            "color: #c0392b; font-size: 11px; font-weight: bold;" if over
            else "color: #7f8c8d; font-size: 11px;"
        )
        self._refresh_controls()

    def _send_chat_message(self):
        target = self.selected_peer
        message = self.chat_input.text().strip()
        if not target or not message or not self.btn_send_chat.isEnabled():
            return  # also swallows Enter on over-length / offline input
        self.chat_input.clear()
        self.adapter.send_chat_async(
            target, message,
            on_success=lambda ok, t=target, m=message: self._on_chat_sent(t, m, bool(ok)),
            on_error=lambda err, t=target, m=message: self._on_chat_sent(t, m, False),
        )

    def _on_chat_sent(self, target: str, message: str, ok: bool):
        """core.send_chat_message records the message in history only on a real
        delivery, so success re-renders from history (also keeps rapid-fire
        sends in true order); failure is shown in place and the text is handed
        back for a retry."""
        if ok:
            if self.selected_peer == target:
                self._render_chat_history(target)
            return
        logger.error(f"Failed to send message to {target}.")
        self.statusBar().showMessage(f"Message to {target} was not delivered.", 6000)
        if self.selected_peer == target:
            self.chat_display.append(
                f"<p style='color:#c0392b; margin:2px 0;'>⚠ Not delivered: "
                f"{html.escape(message)}</p>"
            )
            self._scroll_chat_to_bottom()
            if not self.chat_input.text():
                self.chat_input.setText(message)

    def _render_chat_history(self, uname: str):
        my_uname = self.adapter.my_uname
        blocks = [
            construct_message_html(m, is_self=(m.get("sender") == my_uname))
            for m in self.adapter.history(uname)
        ]
        self.chat_display.setHtml("".join(blocks))
        self._scroll_chat_to_bottom()

    def _scroll_chat_to_bottom(self):
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_message_received(self, msg: dict):
        """Inbound chat (GUI thread). Already stored in core history, so a
        conversation you're not looking at just gets an unread badge and
        re-renders when opened. The desktop popup is _on_notification's job."""
        sender = msg.get("sender", "Unknown")
        if self.selected_peer == sender:
            self.chat_display.append(construct_message_html(msg, is_self=False))
            self._scroll_chat_to_bottom()
            return
        self._unread[sender] = self._unread.get(sender, 0) + 1
        item = self._find_user_item(sender)
        if item is None:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, sender)
            self.user_list.addItem(item)
        self._render_user_item(item, sender)

    def _on_notification(self, title: str, body: str):
        """Desktop popup ONLY when Echo isn't the focused window (Q6); in-window
        cues (unread badges, transfer rows) cover the focused case.
        show_notifications was already honoured by core.notify()."""
        if self.isActiveWindow() and not self.isMinimized():
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

    # =========================================================================
    # Search (5.2.5) + Settings (5.2.4)
    # =========================================================================

    def _open_search_dialog(self):
        from client.ui.file_search_dialog import FileSearchDialog
        FileSearchDialog(
            self.adapter,
            on_go_to_owner=self._select_peer_by_name,
            on_download=self._start_download,
            parent=self,
        ).exec_()

    def _open_settings_dialog(self):
        from client.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.adapter, parent=self)
        if dlg.exec_() and "server_ip" in dlg.changed:
            # A new server address only matters once we connect to it.
            self.statusBar().showMessage("Server address changed — reconnecting…")
            self._begin_connect()  # no-op if an attempt is already in flight

    # =========================================================================
    # Transfers (5.3.5, 5.2.7, 4.7)
    # =========================================================================

    def _create_progress_widget(self, key: str, filename: str, total_size: int,
                                initial_status=TransferStatus.DOWNLOADING,
                                allow_pause: bool = True) -> FileProgressWidget:
        widget = FileProgressWidget(key, filename, total_size,
                                    initial_status=initial_status, allow_pause=allow_pause)
        widget.pause_requested.connect(self._pause_download)
        widget.resume_requested.connect(self._resume_download)
        self.transfers_layout.insertWidget(0, widget)
        self._progress_widgets[key] = widget
        return widget

    def _claim_key(self, key: str) -> bool:
        """True if `key` is free for a new transfer (clearing out a finished or
        failed row that still holds it); False if that transfer is live."""
        widget = self._progress_widgets.get(key)
        if widget is None:
            return True
        if widget.status in _TERMINAL:
            self._remove_progress_widget(key, widget)
            return True
        return False

    def _remove_progress_widget(self, key: str, widget=None):
        """Retire a row. With `widget`, only if that exact widget still owns the
        key — a delayed retire must never remove a newer retry of the same file."""
        current = self._progress_widgets.get(key)
        if current is None or (widget is not None and current is not widget):
            return
        self._progress_widgets.pop(key, None)
        self.transfers_layout.removeWidget(current)
        current.deleteLater()

    def _schedule_retire(self, key: str, delay_ms: int):
        widget = self._progress_widgets.get(key)
        if widget is not None:
            QTimer.singleShot(delay_ms, lambda k=key, w=widget: self._remove_progress_widget(k, w))

    # --- starting downloads ----------------------------------------------------

    def _download_selected(self):
        owner = self._tree_owner
        for data in self._selected_tree_data():
            self._start_download(owner, data)

    def _start_download(self, owner: str, data: dict) -> bool:
        """Download a file or folder from `owner`. Shared by the tree's Download
        button and the search dialog. Returns True if anything started."""
        if not owner or owner == self.adapter.my_uname:
            return False
        if not self._connected or not self.adapter.is_online(owner):
            self.statusBar().showMessage(f"{owner} is offline — can't download right now.", 5000)
            return False
        if data.get("type") == "directory":
            return self._start_folder_download(owner, data)
        return self._start_file_download(owner, data, expected_hash=data.get("hash"))

    def _start_file_download(self, owner: str, file_data: dict, dest_subpath: Optional[str] = None,
                             expected_hash: Optional[str] = None, group: Optional[str] = None) -> bool:
        remote_path = file_data.get("path", "")
        size = file_data.get("size") or 0
        key = self.adapter.transfer_key(owner, remote_path)
        if not self._claim_key(key):
            self.statusBar().showMessage(f"'{file_data.get('name')}' is already downloading.", 4000)
            return False
        self._create_progress_widget(key, file_data.get("name") or remote_path, size)
        if group:
            self._member_group[key] = group
        self.adapter.download_async(
            owner, file_data,
            on_success=lambda ok, k=key: self._on_transfer_done(k, bool(ok)),
            on_error=lambda err, k=key: self._on_transfer_error(k, err),
            dest_subpath=dest_subpath, expected_hash=expected_hash,
        )
        return True

    def _start_folder_download(self, owner: str, folder_data: dict) -> bool:
        """Flatten the folder; one row + worker per file (keyed by that file's
        share path, so progress ticks land) plus ONE aggregate row for the
        folder as a whole (5.3.5's DirProgress)."""
        files: list = []
        get_files_in_dir(folder_data.get("children") or [], files)
        name = folder_data.get("name", "folder")
        if not files:
            self.statusBar().showMessage(f"'{name}' contains no files to download.", 5000)
            return False

        group_key = f"dir::{owner}:{folder_data.get('path', name)}"
        if not self._claim_key(group_key):
            self.statusBar().showMessage(f"'{name}' is already downloading.", 4000)
            return False
        total = sum((f.get("size") or 0) for f in files)
        agg = self._create_progress_widget(group_key, f"📁 {name}/ ({len(files)} files)", total, allow_pause=False)
        group = {"widget": agg, "received": {}, "sizes": {}, "done": set(), "failed": set()}
        self._groups[group_key] = group

        logger.info(f"Downloading folder '{name}' ({len(files)} files) from {owner}.")
        for f in files:
            key = self.adapter.transfer_key(owner, f.get("path", ""))
            if self._start_file_download(owner, f, dest_subpath=f.get("path"),
                                         expected_hash=f.get("hash"), group=group_key):
                group["sizes"][key] = f.get("size") or 0
                group["received"][key] = 0

        if not group["sizes"]:  # every member was already in flight
            self._groups.pop(group_key, None)
            self._remove_progress_widget(group_key, agg)
            return False
        self._update_group(group_key)
        return True

    # --- progress + completion -------------------------------------------------

    def _on_transfer_progress(self, key: str, data: dict):
        """Throttled tick from core (Improvement F) or a terminal status from
        set_transfer_status — the only path for inbound pushes and folder members."""
        widget = self._progress_widgets.get(key)
        if widget is None:
            return
        status = data.get("status")
        widget.update_progress(data)
        if status == TransferStatus.FAILED:
            self._apply_failure_note(widget, key, data.get("reason"))
            self._schedule_retire(key, _RETIRE_FAILED_MS)
        elif status == TransferStatus.COMPLETED:
            self._schedule_retire(key, _RETIRE_DONE_MS)
        self._note_member_progress(key, data.get("progress", 0), status)

    def _on_transfer_done(self, key: str, ok: bool):
        """A download/push worker returned. False is ambiguous for downloads
        (paused OR failed), so the recorded status decides."""
        widget = self._progress_widgets.get(key)
        if widget is None:
            return
        if ok:
            widget.update_progress({"progress": widget.total_size, "total": widget.total_size,
                                    "status": TransferStatus.COMPLETED})
            self._schedule_retire(key, _RETIRE_DONE_MS)
            self._note_member_progress(key, widget.total_size, TransferStatus.COMPLETED)
            return

        rec = self.adapter.get_transfer(key) or {}
        if rec.get("status") == TransferStatus.PAUSED:
            widget.update_progress({"progress": rec.get("progress", 0),
                                    "total": rec.get("total", widget.total_size),
                                    "status": TransferStatus.PAUSED})
            widget.pause_acknowledged()  # the worker has exited: Resume is safe
            return

        widget.update_progress({"progress": rec.get("progress", 0), "total": widget.total_size,
                                "status": TransferStatus.FAILED})
        self._apply_failure_note(widget, key, rec.get("reason"))
        self._schedule_retire(key, _RETIRE_FAILED_MS)
        self._note_member_progress(key, rec.get("progress", 0), TransferStatus.FAILED)

    def _on_transfer_error(self, key: str, err: str):
        """The worker raised (unexpected — transfers catch their own transport
        errors). Fail the row; with no row, don't swallow it silently."""
        logger.error(f"Transfer worker error for {key}: {err}")
        widget = self._progress_widgets.get(key)
        if widget is None:
            ErrorDialog(f"Transfer error: {err}", parent=self).exec_()
            return
        widget.update_progress({"progress": 0, "total": widget.total_size, "status": TransferStatus.FAILED})
        self._schedule_retire(key, _RETIRE_FAILED_MS)
        self._note_member_progress(key, 0, TransferStatus.FAILED)

    def _apply_failure_note(self, widget: FileProgressWidget, key: str, reason: Optional[str]):
        peer = key.partition(":")[0]
        if reason == "declined":
            widget.set_note(f"Declined by {peer}", "#c0392b")
            self.statusBar().showMessage(f"{peer} declined '{widget.filename}'.", 6000)
        elif reason == "unreachable":
            widget.set_note(f"Couldn't reach {peer}", "#c0392b")

    # --- folder aggregate rows ------------------------------------------------

    def _note_member_progress(self, key: str, received: int, status):
        group_key = self._member_group.get(key)
        group = self._groups.get(group_key) if group_key else None
        if group is None or key not in group["sizes"]:
            return
        if status == TransferStatus.COMPLETED:
            group["received"][key] = group["sizes"][key]
            group["done"].add(key)
            group["failed"].discard(key)
        elif status == TransferStatus.FAILED:
            group["failed"].add(key)
        else:
            group["received"][key] = received or 0
            group["failed"].discard(key)
        self._update_group(group_key)

    def _update_group(self, group_key: str):
        group = self._groups.get(group_key)
        if group is None:
            return
        n = len(group["sizes"])
        done, failed = group["done"], group["failed"]
        if len(done) == n:
            status = TransferStatus.COMPLETED
        elif len(done | failed) == n:
            status = TransferStatus.FAILED
        else:
            status = TransferStatus.DOWNLOADING
        widget = group["widget"]
        widget.update_progress({"progress": sum(group["received"].values()),
                                "total": sum(group["sizes"].values()), "status": status})
        if status == TransferStatus.DOWNLOADING:
            widget.set_note(f"{len(done)}/{n} files")
            return
        if status == TransferStatus.FAILED:
            widget.set_note(f"{len(failed)} of {n} files failed", "#c0392b")
        # finished either way: retire the aggregate and drop the bookkeeping
        self._schedule_retire(group_key, _RETIRE_DONE_MS if status == TransferStatus.COMPLETED else _RETIRE_FAILED_MS)
        for member in group["sizes"]:
            self._member_group.pop(member, None)
        self._groups.pop(group_key, None)

    # --- pause / resume ---------------------------------------------------------

    def _pause_download(self, key: str):
        logger.info(f"Pause requested for transfer {key}")
        if not self.adapter.pause_transfer(key):
            widget = self._progress_widgets.get(key)
            if widget:
                widget.revert_to_paused()

    def _resume_download(self, key: str):
        logger.info(f"Resume requested for transfer {key}")
        widget = self._progress_widgets.get(key)
        owner = key.partition(":")[0]
        if not self._connected:
            if widget:
                widget.revert_to_paused("Not connected")
            return
        if not self.adapter.is_online(owner):
            if widget:
                widget.revert_to_paused(f"{owner} is offline")
            return
        started = self.adapter.resume_async(
            key,
            on_success=lambda ok, k=key: self._on_transfer_done(k, bool(ok)),
            on_error=lambda err, k=key: self._on_transfer_error(k, err),
        )
        if not started and widget:
            widget.revert_to_paused("Can't resume this transfer")

    def _restore_journal_transfers(self):
        """Interrupted downloads from a previous session come back as PAUSED
        rows (from the JSON journal, 4.6.4), visibly resumable."""
        for entry in self.adapter.resumable_transfers():
            key = entry["key"]
            if key in self._progress_widgets:
                continue
            filepath = entry.get("filepath", "")
            size = entry.get("total", 0)
            widget = self._create_progress_widget(key, Path(filepath).name or filepath, size,
                                                  initial_status=TransferStatus.PAUSED)
            widget.update_progress({"progress": entry.get("received", 0), "total": size,
                                    "status": TransferStatus.PAUSED})
            widget.set_note(f"Interrupted — from {entry.get('uname', '?')}")

    # --- direct transfer (push) ------------------------------------------------

    def _send_file_directly(self):
        target = self.selected_peer
        if not target or not self._connected or not self.adapter.is_online(target):
            return
        file_path, _ = QFileDialog.getOpenFileName(self, f"Send a file directly to {target}")
        if not file_path:
            return
        p = Path(file_path)
        if not p.is_file():
            return
        key = self.adapter.transfer_key(target, p.name)
        if not self._claim_key(key):
            self.statusBar().showMessage(f"Already sending '{p.name}' to {target}.", 4000)
            return
        widget = self._create_progress_widget(key, f"→ {p.name}", p.stat().st_size, allow_pause=False)
        widget.set_note(f"Waiting for {target} to accept…")
        self.adapter.send_direct_async(
            target, file_path,
            on_success=lambda ok, k=key: self._on_transfer_done(k, bool(ok)),
            on_error=lambda err, k=key: self._on_transfer_error(k, err),
        )

    def _prompt_direct_transfer_consent(self, metadata: dict, ticket):
        """GUI-thread slot for an inbound push (4.7). The peer-listener thread is
        blocked on `ticket.event`; ask here (legal: GUI thread), record the answer,
        and release the listener in `finally` so it can never wedge."""
        try:
            sender = metadata.get("sender") or "A peer"
            # Same naming as the receive loop: safe basename keyed by the
            # resolved sender, so this row matches the key its ticks arrive on.
            safe_name = Path(metadata.get("path", "file")).name
            size = metadata.get("size", 0)

            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Incoming File Transfer")
            msg_box.setText(f"<b>{html.escape(sender)}</b> wants to send you a file:")
            msg_box.setInformativeText(
                f"📄 <b>{html.escape(safe_name)}</b> ({convert_size(size)})<br><br>"
                "Do you want to accept this transfer?"
            )
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.Yes)

            accepted = (msg_box.exec_() == QMessageBox.Yes)
            ticket.accepted = accepted
            if accepted:
                logger.info(f"User accepted direct transfer of '{safe_name}' from {sender}.")
                key = self.adapter.transfer_key(sender, safe_name)
                self._claim_key(key)
                self._create_progress_widget(key, f"← {safe_name}", size, allow_pause=False)
            else:
                logger.info(f"User declined direct transfer of '{safe_name}' from {sender}.")
        except Exception as e:
            logger.error(f"Consent prompt failed: {e}; rejecting transfer.")
            ticket.accepted = False
        finally:
            ticket.event.set()  # release the listener thread with whatever we decided

    # =========================================================================
    # Shutdown (5.1.1 step 4 / 5.4.5)
    # =========================================================================

    def closeEvent(self, event):
        """Pause downloads (journal says PAUSED -> resumable next launch), stop
        the watcher and listener, let workers wind down, close the server socket,
        THEN accept the close."""
        logger.info("Shutting down Echo…")
        self._rescan_timer.stop()
        self.adapter.shutdown()
        event.accept()
