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

    def start(self):
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
    
    def _accept_loop(self):
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
    
    
