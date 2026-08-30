import logging
import threading
from PyQt5.QtCore import QObject, pyqtSignal,QRunnable, QThreadPool,pyqtSlot

from client.peer_listener import PeerListener

# watchdog powers share auto-rescan (5.3.8); kept optional so the app still runs
# (with rescan disabled) if it isn't installed.
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


logger = logging.getLogger(__name__)


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
    """Signals for asynchronous bg tasks."""
    result = pyqtSignal(object)  # Signal to emit the result of the task
    error = pyqtSignal(str)  # Signal to emit an error (exception type, value, traceback)

class Worker(QRunnable):
    """Generic runnable worker to execute blocking network calls off the GUI thread."""

    def __init__(self,fn,*args,**kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @pyqtSlot() #required when connecting signals across threads to avoid seg faults
    def run(self):
        """Run the worker function with the provided arguments."""
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)  # Emit the result signal
        except Exception as e:
            logger.error(f"Error in worker thread: {e}")
            self.signals.error.emit(str(e))

class CoreAdapter(QObject):
    """The Qt-to-Core Bridge:
    -Exposes thread-safe methods to the GUI for interacting with the core.
    -Manages background workers for network operations and keeping UI at 60fps.
    """
    peers_changed = pyqtSignal(dict)  # Signal to notify when the list of peers changes
    message_received = pyqtSignal(dict)  # Signal to notify when a new message is received (sender, message)
    notification = pyqtSignal(str,str)  # Signal to notify the GUI of important events (e.g., errors, status updates)
    transfer_progress = pyqtSignal(str,dict)  # Signal to notify the GUI of file transfer progress (filename, percentage)
    connection_lost = pyqtSignal(str)  # Signal to notify the GUI when the connection to the server is lost
    share_changed = pyqtSignal()  # a change under SHARE_FOLDER_PATH (debounced -> republish, 5.3.8)
    transfer_request = pyqtSignal(object, object)  # 4.7 consent: (metadata, ConsentTicket)

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.thread_pool = QThreadPool()  # Thread pool for managing background workers
        self.share_observer = None  # watchdog Observer watching our share folder (5.3.8)
        self.peer_listener = None   # inbound peer server (chat / file / transfer requests)
        self._wire_core_callbacks()  # Wire up core callbacks to emit signals to the GUI

    def _wire_core_callbacks(self):
        """Wire up core callbacks to emit signals to the GUI."""
        self.core.on_peers_changed = lambda peers: self.peers_changed.emit(peers)
        self.core.on_message_received = lambda message: self.message_received.emit(message)  # Message dict {sender, content}
        self.core.on_notification = lambda title, body: self.notification.emit(title, body)
        self.core.on_transfer_progress = lambda key,prog: self.transfer_progress.emit(key, prog)
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


    #async helpers for blocking core calls

    def run_async(self,fn, on_success = None, on_error = None, *args, **kwargs):
        """Run a blocking function in a background thread and handle its result or error."""
        worker = Worker(fn, *args, **kwargs)
        if on_success:
            worker.signals.result.connect(on_success)
        if on_error:
            worker.signals.error.connect(on_error)
        self.thread_pool.start(worker)

    def browse_async(self,target_uname: str,on_success, on_error = None):
        self.run_async(self.core.browse, on_success, on_error, target_uname)
    def search_async(self,query: str,on_success, on_error = None):
        self.run_async(self.core.search, on_success, on_error, query)
    def send_chat_async(self,target_uname: str,text: str,on_success = None, on_error = None):
        self.run_async(self.core.send_chat_message, on_success, on_error, target_uname, text)
    def publish_share_async(self,on_success = None, on_error = None):
        self.run_async(self.core.publish_share_data, on_success, on_error)

    def connect_and_register_async(self, username: str, server_ip: str, on_done):
        """Connect -> register -> publish share, all off the GUI thread.
        on_done(success: bool, message: str) is delivered on the GUI thread.
        Replaces the one-off QThread the config window used, so the whole app
        runs on ONE worker mechanism (the pool) with the adapter as the only
        core touchpoint."""
        def _flow():
            if not self.core.connect_to_server(server_ip):
                return (False, "Failed to connect to the server. Check the IP address and try again.")
            if not self.core.register(username):
                return (False, "Registration failed — the username may be taken by another host.")
            self.core.publish_share_data()
            return (True, "Registered.")
        self.run_async(
            _flow,
            on_success=lambda result: on_done(*result),
            on_error=lambda err: on_done(False, err),
        )

    # --- inbound peer server -------------------------------------------------

    def start_peer_listener(self):
        """Start the inbound peer server (listens on CLIENT_RECV_PORT for chat,
        file requests, and direct transfers). Idempotent; without it no inbound
        P2P — including chat — can ever arrive."""
        if self.peer_listener is not None:
            return
        self.peer_listener = PeerListener(self.core)
        self.peer_listener.start()

    # --- share auto-rescan (5.3.8) ------------------------------------------

    def start_share_watch(self):
        """Watch SHARE_FOLDER_PATH recursively; on ANY change emit share_changed
        (thread-safe — the handler runs on watchdog's own thread and only pokes
        the signal). The UI debounces that into one publish_share_data. No-op if
        watchdog isn't installed or we're already watching."""
        if not _WATCHDOG_AVAILABLE:
            logger.warning("watchdog not installed — share auto-rescan disabled.")
            return
        if self.share_observer is not None:
            return
        share_path = self.core.settings.get("share_folder_path")
        if not share_path:
            return
        adapter = self

        class _ShareHandler(FileSystemEventHandler):
            def on_any_event(self, event):
                adapter.share_changed.emit()

        self.share_observer = Observer()
        self.share_observer.schedule(_ShareHandler(), share_path, recursive=True)
        self.share_observer.daemon = True
        self.share_observer.start()
        logger.info(f"Share watcher started on {share_path}")

    def stop_share_watch(self):
        """Stop the share watcher (call on shutdown, or before re-pointing it at
        a new share folder). The Observer is NOT a daemon by contract, so join it
        so it can't outlive the window."""
        if self.share_observer is not None:
            try:
                self.share_observer.stop()
                self.share_observer.join(timeout=2)
            except Exception as e:
                logger.error(f"Error stopping share watcher: {e}")
            self.share_observer = None
