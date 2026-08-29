import logging
from PyQt5.QtCore import QObject, pyqtSignal,QRunnable, QThreadPool,pyqtSlot


logger = logging.getLogger(__name__)

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

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.thread_pool = QThreadPool()  # Thread pool for managing background workers
        self._wire_core_callbacks()  # Wire up core callbacks to emit signals to the GUI

    def _wire_core_callbacks(self):
        """Wire up core callbacks to emit signals to the GUI."""
        self.core.on_peers_changed = lambda peers: self.peers_changed.emit(peers)
        self.core.on_message_received = lambda message: self.message_received.emit(message)  # Message dict {sender, content}
        self.core.on_notification = lambda title, body: self.notification.emit(title, body)
        self.core.on_transfer_progress = lambda key,prog: self.transfer_progress.emit(key, prog)
        self.core.on_connection_lost = lambda reason: self.connection_lost.emit(reason)


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
