import logging
import socket
import threading
from typing import TYPE_CHECKING

from utils.constants import CLIENT_RECV_PORT,FMT
from utils.exceptions import RequestException, ExceptionCodes
from utils.protocol import receive_message, send_error
from utils.types import HeaderCode

#Import only for type hints: a runtime import would create a circular
#dependency once ClientCore starts instantiating PeerListener.
if TYPE_CHECKING:
    from client.core import ClientCore


logger = logging.getLogger(__name__)

class PeerListener:
    def __init__(self, core: "ClientCore") -> None:
        self.core = core
        self.listen_socket = None
        self.running = False
        self.thread = None

    def start(self) -> None:
        """ Start the peer listener in a separate thread in the background. """

        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            #Bind to empty string to accept conn from any interface

            self.listen_socket.bind(('', CLIENT_RECV_PORT))
            self.listen_socket.listen(5)
            self.running = True

            self.thread = threading.Thread(target = self._accept_loop, daemon=True)
            self.thread.start()
            logger.info(f"Peer listener started on port {CLIENT_RECV_PORT}.")

        except OSError as e:
            logger.error(f"Failed to start peer listener: {e}", exc_info=True)

            self.listen_socket = None

    def _accept_loop(self) -> None:
        """ Accept incoming connections and handle them with threads."""

        while self.running:
            try:
                conn,addr = self.listen_socket.accept()
                logger.debug(f"Accepted connection from {addr}.")

                #Handle each connection in a new thread. addr is (ip, port);
                #pass just the ip string to the handler.
                t = threading.Thread(target=self._handle_connection, args=(conn, addr[0]), daemon=True)
                t.start()

            except OSError as e:
                # A closed listener socket (from stop()) surfaces here as the
                # normal shutdown path; anything else is a genuine error.
                if self.running:
                    logger.error(f"Peer listener accept loop error: {e}", exc_info=True)
                break

    def _handle_connection(self, conn:socket.socket, peer_ip: str):

        """ Handle an incoming connection from a peer. """
        try:
            msg = receive_message(conn)

            match msg['type']:
                case HeaderCode.MESSAGE:
                    #Handle incoming message
                    self._handle_chat_message(peer_ip,msg['query'].decode(FMT))
                case HeaderCode.FILE_REQUEST:
                    #Handle incoming file request
                    self._handle_file_request(conn,peer_ip,msg['query'])
                case HeaderCode.DIRECT_TRANSFER_REQUEST:
                    #Handle incoming direct transfer request
                    self._handle_direct_transfer_request(conn,peer_ip,msg['query'])
                case _:
                    logger.warning(f"Received unknown message type from {peer_ip}: {msg['type']}")
                    send_error(conn, RequestException("Unknown message type", ExceptionCodes.BAD_REQUEST))

        except RequestException as e:
            logger.warning(f"RequestException while handling connection from {peer_ip}: {e.msg}")
            # A DISCONNECT means the peer is already gone — replying would just
            # raise OSError on a dead socket. Only reply for other errors, and
            # guard that send too in case the connection drops mid-reply.
            if e.code != ExceptionCodes.DISCONNECT:
                try:
                    send_error(conn, e)
                except OSError as send_err:
                    logger.debug(f"Failed to send error reply to {peer_ip}: {send_err}")
        except Exception as e:
            logger.error(f"Unexpected error while handling connection from {peer_ip}: {e}", exc_info=True)

        finally:
            conn.close()

    def _handle_chat_message(self, peer_ip: str, text: str) -> None:

        """ Processes inbound p2p chat messages. """
        sender = self.core.request_peer_uname(peer_ip) or peer_ip
        logger.info(f"Received message from {sender}: {text}")

        self.core.add_message_to_history(sender, text, is_self=False)

        # TODO: Trigger notification (Step 4.2.4)

    def _handle_file_request(self, conn: socket.socket, peer_ip: str, query: bytes) -> None:

        #Placeholder for session 5 file uploads
        pass

    def _handle_direct_transfer_request(self, conn: socket.socket, peer_ip: str, query: bytes) -> None:

        #Placeholder for session 8 pushes
        pass

    def stop(self) -> None:
        """ Stop the peer listener and close the socket. """
        self.running = False
        if self.listen_socket:
            try:
                self.listen_socket.close()
            except OSError as e:
                logger.debug(f"Error closing peer listener socket: {e}")
            self.listen_socket = None
            logger.info("Peer listener stopped.")
    
    