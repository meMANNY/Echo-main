import logging
import socket
import threading

from utils.constants import CLIENT_RECV_PORT,FMT
from utils.exceptions import RequestException, ExceptionCodes
from utils.protocol import receive_message, send_error
from utils.types import HeaderCode


logger = logging.getLogger(__name__)

class PeerListener:
    def __init__(self, core):
        self.core = core 
        self.server_socket = None
        self.running = False
        self.thread = None

    def start(self) -> None:
        """ Start the peer listener in a separate thread in the background. """
        
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            #Bind to empty string to accept conn from any interface

            self.server_socket.bind(('', CLIENT_RECV_PORT))
            self.server_socket.listen(5)
            self.running = True

            self.thread = threading.Thread(target = self._accept_loop, daemon=True)
            self.thread.start()
            logger.info(f"Peer listener started on port {CLIENT_RECV_PORT}.")
        
        except OSError as e:
            logger.error(f"Failed to start peer listener: {e}", exc_info=True)
            
            self.server_socket = None
    
    def _accept_loop(self) -> None:
        """ Accept incoming connections and handle them with threads."""

        while self.running:
            try:
                conn,addr = self.server_socket.accept()
                logger.debug(f"Accepted connection from {addr}.")

                #Handle each connection in a new thread
                t = threading.Thread(target=self._handle_connection, args=(conn, addr), daemon=True)
                t.start()
            
            except OSError as e:
                break  # Socket closed, exit loop
    
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
            send_error(conn, e)
        except Exception as e:
            logger.error(f"Unexpected error while handling connection from {peer_ip}: {e}", exc_info=True)
        
        finally:
            conn.close()
    
    def _handle_chat_message(self, peer_ip: str, text: str) -> None:

        """ Processes inbound p2p chat messages. """
        sender = self.core.request_uname(peer_ip) or peer_ip
        logger.info(f"Received message from {sender}: {text}")

        # 1. TODO: Append to chat history (Step 4.2.3)
        # 2. TODO: Trigger notification (Step 4.2.4)
    
    def _handle_file_request(self, conn: socket.socket, peer_ip: str, query: bytes) -> None:

        #Placeholder for session 5 file uploads
        pass
    
    def _handle_direct_transfer_request(self, conn: socket.socket, peer_ip: str, query: bytes) -> None:

        #Placeholder for session 8 pushes
        pass

    def stop(self) -> None:
        """ Stop the peer listener and close the socket. """
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError as e:
                pass
            self.server_socket = None
            logger.info("Peer listener stopped.")
        
        


