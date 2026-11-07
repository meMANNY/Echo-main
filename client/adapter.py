import logging
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot

from client import transfers
from client.peer_listener import PeerListener
from utils.exceptions import ExceptionCodes
from utils.helpers import get_unique_filename

# watchdog powers share auto-rescan (5.3.8); kept optional so the app still runs
# (with rescan disabled) if it isn't installed.
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


logger = logging.getLogger(__name__)

# How many transfers stream at once. Transfers get their OWN pool so a folder
# download can never occupy every worker and starve browse/search/chat calls
# queued behind it (those would otherwise wait for whole files to finish).
_MAX_PARALLEL_TRANSFERS = 4


class ConsentTicket:
    """Carries an inbound-transfer consent question across the thread boundary.
    The peer-listener thread emits (metadata, ticket), then blocks on `event`;
    the GUI-thread slot pops the dialog, writes `accepted`, and sets `event`,
    releasing the listener with the user's answer."""
    __slots__ = ("event", "accepted")

    def __init__(self):
        self.event = threading.Event()
        self.accepted = False


class WorkerSignals(QObject):
    """QRunnable isn't a QObject, so it can't own signals (plan 5.4.3) — each
    runnable carries one of these bundles instead."""
    result = pyqtSignal(object)  # the task's return value
    error = pyqtSignal(str)      # str(exception) if the task raised


class Worker(QRunnable):
    """Generic runnable worker to execute blocking calls off the GUI thread."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as e:
            logger.error(f"Error in worker thread: {e}", exc_info=True)
            self.signals.error.emit(str(e))


class CoreAdapter(QObject):
    """The ONE Qt<->core seam (5.1.2).

    - Forwards core callbacks out as signals (safe to emit from any thread).
    - Runs every blocking core call in a pool worker, never on the GUI thread.
    - Is the only thing widgets talk to: they read state and start work through
      the methods below and never reach into ClientCore or client.transfers.
    """
    peers_changed = pyqtSignal(dict)          # uname -> last_seen (online only)
    message_received = pyqtSignal(dict)       # {sender, content}
    notification = pyqtSignal(str, str)       # title, body
    transfer_progress = pyqtSignal(str, dict) # transfer key, TransferProgress snapshot
    connection_lost = pyqtSignal(str)         # reason
    share_changed = pyqtSignal()              # raw watcher event (UI debounces it)
    share_republished = pyqtSignal(bool)      # a publish_share_data finished (ok?)
    transfer_request = pyqtSignal(object, object)  # 4.7 consent: (metadata, ConsentTicket)

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.thread_pool = QThreadPool()      # short server round-trips
        self.transfer_pool = QThreadPool()    # long-running file transfers
        self.transfer_pool.setMaxThreadCount(_MAX_PARALLEL_TRANSFERS)
        self.share_observer = None  # watchdog Observer watching our share folder (5.3.8)
        self.peer_listener = None   # inbound peer server (chat / file / transfer requests)
        self._wire_core_callbacks()

    def _wire_core_callbacks(self):
        self.core.on_peers_changed = lambda peers: self.peers_changed.emit(peers)
        self.core.on_message_received = lambda message: self.message_received.emit(message)
        self.core.on_notification = lambda title, body: self.notification.emit(title, body)
        self.core.on_transfer_progress = lambda key, prog: self.transfer_progress.emit(key, prog)
        self.core.on_connection_lost = lambda reason: self.connection_lost.emit(reason)
        # 4.7 consent: core asks THIS (on the listener thread); we bounce the
        # question to the GUI thread and block until it answers. Core never sees Qt.
        self.core.on_direct_transfer_request = self._request_transfer_consent

    def _request_transfer_consent(self, metadata) -> bool:
        """Runs on the peer-listener thread. Popping a dialog here would break the
        GUI-thread rule, so hand the question to the GUI thread via a queued signal
        and wait on the ticket for the user's decision. A timeout defaults to reject
        so a closed/hung window can never wedge the listener thread."""
        ticket = ConsentTicket()
        self.transfer_request.emit(metadata, ticket)
        if not ticket.event.wait(timeout=120):
            logger.warning("Direct-transfer consent prompt timed out; rejecting.")
            return False
        return ticket.accepted

    # --- read-only state (GUI thread, lock-free snapshots) -------------------

    @property
    def settings(self) -> dict:
        """A copy — edits go through update_settings(), never by mutation."""
        return dict(self.core.settings)

    @property
    def my_uname(self) -> str:
        return self.core.settings.get("uname", "")

    @property
    def connected(self) -> bool:
        return self.core.connected

    def is_online(self, uname: Optional[str]) -> bool:
        return bool(uname) and uname in self.core.online_peers

    def history(self, uname: str) -> list:
        return list(self.core.message_history.get(uname, []))

    def get_transfer(self, key: str):
        return self.core.get_transfer(key)

    def resumable_transfers(self) -> list:
        """Journal entries left PAUSED/DOWNLOADING by a previous session, each
        with `key` and a truthful `received` — the partial temp file's size IS
        the resume state (the journal's own `received` is only the offset the
        last attempt started from)."""
        out = []
        for entry in self.core.get_resumable_transfers():
            owner, path = entry.get("uname", ""), entry.get("filepath", "")
            temp = transfers._temp_path_for(owner, path)
            try:
                received = temp.stat().st_size if temp.exists() else 0
            except OSError:
                received = entry.get("received", 0)
            out.append({**entry, "key": transfers._transfer_key(owner, path), "received": received})
        return out

    @staticmethod
    def transfer_key(owner: str, path: str) -> str:
        return transfers._transfer_key(owner, path)

    @property
    def watcher_active(self) -> bool:
        return self.share_observer is not None

    # --- settings -------------------------------------------------------------

    def update_settings(self, changes: dict) -> set:
        """Merge `changes` into the settings and persist them ONCE through
        core.save_settings. If the share folder moved, re-point the watcher and
        republish so both the automatic sync and the server index follow the
        setting (5.2.4). Returns the set of keys whose value actually changed."""
        old = dict(self.core.settings)
        changed = {k for k, v in changes.items() if old.get(k) != v}
        if not changed:
            return changed
        new_settings = {**old, **changes}
        self.core.save_settings(new_settings)
        logger.info(f"Settings updated: {sorted(changed)}")
        if "share_folder_path" in changed:
            self.stop_share_watch()
            self.start_share_watch()
            if self.connected:
                self.publish_share_async()
        return changed

    # --- async helpers for blocking core calls -------------------------------

    def run_async(self, fn, on_success=None, on_error=None, *args, pool=None, **kwargs):
        """Run a blocking function in a pool worker; deliver its result/error to
        the callbacks on the GUI thread (queued signal)."""
        worker = Worker(fn, *args, **kwargs)
        if on_success:
            worker.signals.result.connect(on_success)
        if on_error:
            worker.signals.error.connect(on_error)
        (pool or self.thread_pool).start(worker)

    def browse_async(self, target_uname: str, on_success, on_error=None):
        self.run_async(self.core.browse, on_success, on_error, target_uname)

    def search_async(self, query: str, on_success, on_error=None):
        self.run_async(self.core.search, on_success, on_error, query)

    def send_chat_async(self, target_uname: str, text: str, on_success=None, on_error=None):
        self.run_async(self.core.send_chat_message, on_success, on_error, target_uname, text)

    def publish_share_async(self, on_success=None, on_error=None):
        """Republish our whole share tree. Every outcome is also broadcast as
        share_republished(ok), so the UI's status cue / own-tree redraw works no
        matter who triggered the publish (watcher, Share button, settings)."""
        def _done(ok):
            self.share_republished.emit(bool(ok))
            if on_success:
                on_success(ok)

        def _failed(err):
            self.share_republished.emit(False)
            if on_error:
                on_error(err)

        self.run_async(self.core.publish_share_data, _done, _failed)

    def connect_and_register_async(self, username: str, server_ip: str, on_done):
        """Connect -> register -> publish share, all off the GUI thread.
        on_done(success: bool, message: str, reason: str) runs on the GUI thread;
        reason is "ok", "connect", "user_exists" or "register" so the caller can
        pick the right recovery (fix the IP vs. pick another username)."""
        def _flow():
            if not self.core.connect_to_server(server_ip):
                return (False, "Could not reach the server at "
                        f"{server_ip}. Check the IP address and that the server is running.",
                        "connect")
            if not self.core.register(username):
                if self.core.last_register_error == ExceptionCodes.USER_EXISTS:
                    return (False, f"The username '{username}' is already taken by another "
                            "computer. Please choose a different one.", "user_exists")
                return (False, "Connected, but registration failed. The connection may have "
                        "dropped. Please try again.", "register")
            self.core.publish_share_data()
            return (True, "Registered.", "ok")

        self.run_async(
            _flow,
            on_success=lambda result: on_done(*result),
            on_error=lambda err: on_done(False, err, "connect"),
        )

    # --- transfers --------------------------------------------------------------

    def download_async(self, owner: str, file_data: dict, on_success=None, on_error=None,
                       dest_subpath: Optional[str] = None, expected_hash: Optional[str] = None):
        """Download one file from `owner` on the transfer pool. on_success(bool):
        True = verified + moved into Downloads; False = paused OR failed (check
        get_transfer(key)["status"] to tell which)."""
        self.run_async(
            transfers.download_file, on_success, on_error,
            self.core, owner, file_data.get("path", ""), file_data.get("size") or 0,
            0, expected_hash, dest_subpath,
            pool=self.transfer_pool,
        )

    def resume_async(self, key: str, on_success=None, on_error=None) -> bool:
        """Resume a paused/interrupted download. Prefers the journal entry: it
        keeps the hash and dest_subpath the in-memory record drops, so a folder
        member lands back in its mirrored place and still gets verified.
        Returns False (nothing started) if the key isn't a known download."""
        owner, _, remote_path = key.partition(":")
        entry = self.core.get_journal_entry(key)
        rec = self.core.get_transfer(key)
        source = entry or rec
        if not owner or not remote_path or source is None:
            return False
        meta = {
            "path": remote_path,
            "size": source.get("total", 0),
            "hash": entry.get("hash") if entry else None,
        }
        dest_subpath = entry.get("dest_subpath") if entry else None
        self.run_async(
            transfers.resume_download, on_success, on_error,
            self.core, owner, meta, dest_subpath,
            pool=self.transfer_pool,
        )
        return True

    def pause_transfer(self, key: str) -> bool:
        return self.core.pause_transfer(key)

    def send_direct_async(self, target_uname: str, local_path: str, on_success=None, on_error=None):
        """Push a local file to a peer (4.7). Waits on the receiver's consent
        dialog first, so it runs on the transfer pool."""
        self.run_async(
            transfers.send_direct_transfer, on_success, on_error,
            self.core, target_uname, local_path,
            pool=self.transfer_pool,
        )

    # --- importing into our own share ---------------------------------------

    def import_into_share_async(self, paths: list, on_success=None, on_error=None):
        """Copy files/folders into the share folder off the GUI thread (a big
        copy would freeze the window). on_success((copied, skipped)) with lists of
        names. The watcher sees the new files and republishes through the usual
        debounce; without a watcher we republish here so the import still
        reaches the server."""
        share_root = Path(self.core.settings["share_folder_path"])

        def _copy():
            share_resolved = share_root.resolve()
            share_resolved.mkdir(parents=True, exist_ok=True)
            copied, skipped = [], []
            for raw in paths:
                src = Path(raw)
                try:
                    src_resolved = src.resolve()
                except OSError:
                    skipped.append(src.name)
                    continue
                # Refuse anything already inside the share, and any folder that
                # CONTAINS the share (copying it in would recurse forever).
                if (src_resolved == share_resolved
                        or src_resolved.is_relative_to(share_resolved)
                        or share_resolved.is_relative_to(src_resolved)):
                    skipped.append(src.name)
                    continue
                dest = Path(get_unique_filename(share_root / src.name))
                if src.is_dir():
                    shutil.copytree(src, dest)
                elif src.is_file():
                    shutil.copy2(src, dest)
                else:
                    skipped.append(src.name)
                    continue
                copied.append(dest.name)
            if copied and not self.watcher_active:
                self.core.publish_share_data()
            return copied, skipped

        self.run_async(_copy, on_success, on_error)

    # --- inbound peer server -------------------------------------------------

    def start_peer_listener(self) -> bool:
        """Start the inbound peer server (CLIENT_RECV_PORT: chat, file requests,
        direct transfers). Idempotent. Returns False if it couldn't bind (most
        often: another Echo instance already owns the port) — without it no
        inbound P2P, including chat, can arrive, so the UI must say so."""
        if self.peer_listener is not None and self.peer_listener.running:
            return True
        self.peer_listener = PeerListener(self.core)
        self.peer_listener.start()
        return self.peer_listener.running

    # --- share auto-rescan (5.3.8) ------------------------------------------

    def start_share_watch(self):
        """Watch the share folder recursively; on ANY change emit share_changed
        (thread-safe — the handler runs on watchdog's own thread and only pokes
        the signal). The UI debounces that into one publish_share_data. No-op if
        watchdog isn't installed or we're already watching."""
        if not _WATCHDOG_AVAILABLE:
            logger.warning("watchdog not installed — share auto-rescan disabled.")
            return
        if self.share_observer is not None:
            return
        share_path = self.core.settings.get("share_folder_path")
        if not share_path or not Path(share_path).is_dir():
            logger.warning(f"Share folder '{share_path}' missing — auto-rescan disabled.")
            return
        adapter = self

        class _ShareHandler(FileSystemEventHandler):
            def on_any_event(self, event):
                adapter.share_changed.emit()

        try:
            observer = Observer()
            observer.schedule(_ShareHandler(), share_path, recursive=True)
            observer.daemon = True
            observer.start()
        except Exception as e:
            logger.error(f"Could not start share watcher on {share_path}: {e}")
            return
        self.share_observer = observer
        logger.info(f"Share watcher started on {share_path}")

    def stop_share_watch(self):
        """Stop the share watcher (shutdown, or before re-pointing it at a new
        share folder). Joined so it can't outlive the window."""
        if self.share_observer is not None:
            try:
                self.share_observer.stop()
                self.share_observer.join(timeout=2)
            except Exception as e:
                logger.error(f"Error stopping share watcher: {e}")
            self.share_observer = None

    # --- shutdown (5.4.5) -----------------------------------------------------

    def shutdown(self):
        """Ordered teardown: pause downloads so the journal records them as
        PAUSED (resumable next launch), stop the watcher, stop the listener, give
        workers a moment to notice and exit, then close the server socket."""
        paused = self.core.pause_active_downloads()
        if paused:
            logger.info(f"Paused {len(paused)} active download(s) for next launch.")
        self.stop_share_watch()
        if self.peer_listener is not None:
            try:
                self.peer_listener.stop()
            except Exception as e:
                logger.error(f"Error stopping peer listener: {e}")
        # Paused workers exit on their next chunk; anything stuck in a socket
        # call is a daemon-ish pool thread we won't wait on forever.
        self.transfer_pool.waitForDone(3000)
        self.thread_pool.waitForDone(1000)
        try:
            self.core.server.close()
        except Exception as e:
            logger.error(f"Error closing server connection: {e}")
