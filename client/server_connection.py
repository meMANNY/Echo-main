import logging
import socket
import threading
from typing import Callable, Optional

from utils.constants import SERVER_RECV_PORT
from utils.exceptions import ExceptionCodes, RequestException
from utils.protocol import receive_message, send_msgpack, send_text
from utils.types import HeaderCode, SocketMessage

logger = logging.getLogger(__name__)

# Transport-level failures mean the socket itself is no longer trustworthy, so
# we tear it down rather than keep issuing requests on a dead connection.
TRANSPORT_FAILURE_CODES = (
    ExceptionCodes.DISCONNECT,
    ExceptionCodes.INVALID_HEADER,
    ExceptionCodes.INCOMPLETE,
)


class ServerConnection:
    """Owns the persistent server socket and the lock that serializes it.

    Improvement A: every send/recv pair on the server socket goes through
    request() or run(), each of which holds the lock for the WHOLE exchange.
    That makes forgetting to lock impossible (the trap the hand-repeated
    `with server_lock` pattern invited), and centralizes transport-failure
    teardown in ONE place instead of copy-pasting it into every caller.

    Kept free of ClientCore and Qt so it stays a unit you can test on its own.
    """

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        # Fired (with a reason string) ONLY on an unintended transport teardown,
        # never on a deliberate connect()/close(). ClientCore points this at its
        # on_connection_lost hook so the UI can raise a disconnected banner.
        self.on_teardown: Optional[Callable[[str], None]] = None

    @property
    def is_open(self) -> bool:
        """True while a live server socket exists (cleared on teardown)."""
        return self._sock is not None

    def connect(self, server_ip: str, timeout: float = 5.0) -> bool:
        """Open a fresh connection, replacing any existing one. Never raises;
        returns True on success, False on refusal/timeout."""
        with self._lock:
            self._teardown()  # drop any stale socket first
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(timeout)
                logger.info(f"Connecting to server at {server_ip}:{SERVER_RECV_PORT} (timeout {timeout}s)...")
                sock.connect((server_ip, SERVER_RECV_PORT))
                sock.settimeout(None)  # blocking again once connected
            except (socket.timeout, OSError) as e:
                try:
                    sock.close()
                except OSError:
                    pass
                if isinstance(e, socket.timeout):
                    logger.error(f"Connection to server at {server_ip}:{SERVER_RECV_PORT} timed out.")
                else:
                    logger.error(f"Failed to connect to server at {server_ip}:{SERVER_RECV_PORT}: {e}")
                return False
            self._sock = sock
            logger.info(f"Connected to server at {server_ip}:{SERVER_RECV_PORT}")
            return True

    def request(self, type_code: HeaderCode, body, *, msgpack_body: bool = False) -> SocketMessage:
        """Send one framed message and return the server's reply, holding the
        lock across the whole send/recv pair. Raises RequestException (including
        a server 'e' reply) or OSError; a transport failure tears the socket
        down first, so the next call sees is_open == False.

        body is a str for the default text frame, or any msgpack-able object
        when msgpack_body=True."""
        def _do(sock: socket.socket) -> SocketMessage:
            if msgpack_body:
                send_msgpack(sock, type_code, body)
            else:
                send_text(sock, type_code, body)
            return receive_message(sock)

        with self._lock:
            return self._guarded(_do)

    def run(self, fn: Callable[[socket.socket], object]):
        """Run fn(sock) under the lock — for the multi-step helpers (request_ip,
        request_uname, update_share_data) that drive the raw socket themselves.
        Same teardown-on-transport-failure guarantee as request()."""
        with self._lock:
            return self._guarded(fn)

    def close(self) -> None:
        """Close the server socket (e.g. on app shutdown)."""
        with self._lock:
            self._teardown()

    # --- internal helpers: the lock is already held when these run ---

    def _guarded(self, fn: Callable[[socket.socket], object]):
        if self._sock is None:
            raise RequestException(msg="Not connected to server", code=ExceptionCodes.DISCONNECT)
        try:
            return fn(self._sock)
        except RequestException as e:
            # Only a transport-level failure means the socket is dead; a
            # semantic error (USER_EXISTS, NOT_FOUND, ...) leaves it usable.
            if e.code in TRANSPORT_FAILURE_CODES:
                self._teardown()
                self._notify_lost(str(e))
            raise
        except OSError as e:
            self._teardown()
            self._notify_lost(str(e))
            raise

    def _notify_lost(self, reason: str) -> None:
        """Tell the owner the connection dropped. The callback only emits a
        queued Qt signal, so calling it under the lock is safe (non-blocking,
        no re-entrancy)."""
        cb = self.on_teardown
        if cb is not None:
            try:
                cb(reason)
            except Exception as e:
                logger.error(f"on_teardown callback failed: {e}")

    def _teardown(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
                logger.debug("Server socket closed.")
            except OSError as e:
                logger.error(f"Error closing server socket: {e}")
            self._sock = None
