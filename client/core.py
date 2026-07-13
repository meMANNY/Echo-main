import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Optional

import msgpack

from utils.constants import (
    CLIENT_SEND_PORT,
    FMT,
    RECV_FOLDER_PATH,
    SERVER_RECV_PORT,
    SHARE_COMPRESSED_PATH,
    SHARE_FOLDER_PATH,
    TEMP_FOLDER_PATH,
    DIRECT_TEMP_FOLDER_PATH,
    USER_SETTINGS_PATH,
    HEARTBEAT_TIMER,
    ONLINE_TIMEOUT
)
from utils.exceptions import ExceptionCodes, RequestException
from utils.protocol import send_text, receive_message, send_msgpack
from utils.socket_functions import update_share_data
from utils.types import UserSettings, HeaderCode


logger = logging.getLogger(__name__)


class ClientCore:
    def __init__(self):

        logger.info("Initializing ClientCore")

        #State properties
        self.server_socket: Optional[socket.socket] = None
        self.connected: bool = False

        self.server_lock = threading.Lock()

        #Peer tracking

        self.online_peers: dict[str, dict] = {}  # Dictionary to track online peers and their details

        self.first_run: bool = False  # Flag to indicate if it's the first run of the application
        self._setup_directories()
        self.settings: UserSettings = self._load_settings()

        self.online_peers: dict[str, float] = {}  # Dictionary to track online peers and their details
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop,daemon=True)
        self.heartbeat_thread.start()
        logger.info("Heartbeat thread started.")

        logger.info(
            f"ClientCore initialized (first_run={self.first_run}, "
            f"uname='{self.settings['uname']}', server_ip='{self.settings['server_ip']}')"
        )
    
    def _setup_directories(self) -> None:
        """ Idempotently creates all necessary directories for the client. """

        dirs = {
            SHARE_FOLDER_PATH,
            RECV_FOLDER_PATH,
            TEMP_FOLDER_PATH,
            SHARE_COMPRESSED_PATH,
            DIRECT_TEMP_FOLDER_PATH,
            USER_SETTINGS_PATH.parent, #~/.Echo/db/
        }

        for d in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Directory {d} created or already exists.")
            except Exception as e:
                logger.error(f"Failed to create directory {d}: {e}", exc_info=True)
                
    
    def _load_settings(self) -> UserSettings:
        """ Loads user settings from a JSON file. If the file doesn't exist, it creates default settings. """

        default_settings: UserSettings = {
            "uname": "",
            "share_folder_path": str(SHARE_FOLDER_PATH),
            "server_ip": "127.0.0.1",
            "downloads_folder_path": str(RECV_FOLDER_PATH),
            "show_notifications": True,
        }

        if not USER_SETTINGS_PATH.exists():
            logger.info("User settings file not found. First Run detected. Creating default settings.")
            self.first_run = True
            return default_settings

        try:
            with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f) #becomes a python dict

                #Guard against valid JSON that isn't an object (e.g. [], 42, "x")
                #before calling .get() / merging, otherwise those would raise.
                if not isinstance(loaded, dict):
                    logger.error("User settings file is not a valid dictionary. Treating as first run.")
                    self.first_run = True
                    return default_settings

                #File exists but no username.
                if not loaded.get("uname"):
                    logger.info("User settings file found but username is empty. First Run detected.")
                    self.first_run = True
                else:
                    self.first_run = False

                logger.debug("User settings loaded successfully.")
                #Merge loaded settings with defaults to ensure all keys are present
                return {**default_settings, **loaded}

        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load user settings: {e}. Treating as first run.", exc_info=True)
            self.first_run = True
            return default_settings
    
    def save_settings(self, new_settings: UserSettings) -> None:
        """Saves the settings to dict and updated memory"""

        self.settings = new_settings
        logger.debug(f"Persisting settings for uname='{new_settings['uname']}'")
        try:
            with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
                logger.info("User settings saved successfully.")
        except OSError as e:
            logger.error(f"Failed to save user settings: {e}")
    
    def connect_to_server(self,server_ip: str) -> bool:
        """ Established a socket connection to the central server."""
        with self.server_lock:
            #IF already connected, close the existing socket before creating a new one
            if self.server_socket:
                try:
                    self.server_socket.close()
                    self.connected = False
                    logger.info("Closed existing server socket before establishing a new connection.")
                except OSError as e:
                    logger.error(f"Error closing existing server socket: {e}", exc_info=True)
                    

            try:
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                CONNECTION_TIMEOUT = 5  # seconds
                self.server_socket.settimeout(CONNECTION_TIMEOUT)
                logger.info(f"Attempting to connect to server at {server_ip}:{SERVER_RECV_PORT} with timeout {CONNECTION_TIMEOUT}s...")
                #Bind local port if requested or let os choose
                self.server_socket.connect((server_ip, SERVER_RECV_PORT))
                self.server_socket.settimeout(None)  # Remove timeout after successful connection
                self.settings["server_ip"] = server_ip
                self.save_settings(self.settings)  # Persist the new server IP
                logger.info(f"Connected to server at {server_ip}:{SERVER_RECV_PORT}")
                return True
            except (socket.timeout, OSError) as e:
                if self.server_socket:
                    try:
                        self.server_socket.close()
                        logger.debug("Server socket closed after failed connection attempt.")
                    except OSError as close_error:
                        logger.error(f"Error closing server socket after failed connection: {close_error}", exc_info=True)
                        
                self.server_socket = None
                if isinstance(e, socket.timeout):
                    logger.error(f"Connection to server at {server_ip}:{SERVER_RECV_PORT} timed out.")
                else:
                    logger.error(f"Failed to connect to server at {server_ip}:{SERVER_RECV_PORT}: {e}", exc_info=True)
                return False
        
    def _teardown_server_socket(self) -> None:
        """ Closes and clears the server socket after a transport failure.
        Caller must already hold server_lock. """

        if self.server_socket:
            try:
                self.server_socket.close()
                logger.debug("Server socket closed after transport failure.")
            except OSError as e:
                logger.error(f"Error closing server socket: {e}", exc_info=True)
        self.server_socket = None
        self.connected = False

    def register(self, username: str) -> bool:
        """ Performs the new connection registration with the server. Returns True if successful, False otherwise. """

        if not self.server_socket:
            logger.error("Cannot register: No server connection established.")
            return False
        
        with self.server_lock:
            try:
                #Send registration request
                send_text(self.server_socket, HeaderCode.NEW_CONNECTION,username)

                #Wait for server response
                response = receive_message(self.server_socket)

                if response["type"] == HeaderCode.NEW_CONNECTION:
                    logger.info(f"Registration successful for username='{username}'")
                    self.settings["uname"] = username
                    self.save_settings(self.settings)  # Persist the new username
                    self.connected = True
                    self.first_run = False  # Update first_run flag after successful registration
                    return True
                
                else:
                    logger.error(f"Unexpected response from server during registration: {response}")
                    return False
                
            except RequestException as e:
                if e.code == ExceptionCodes.USER_EXISTS:
                    logger.warning(f"Registration failed: Username '{username}' already taken by another host.")
                elif e.code == ExceptionCodes.BAD_REQUEST:
                    #reconnection
                    self.settings["uname"] = username
                    self.save_settings(self.settings)  # Persist the new username
                    self.connected = True
                    self.first_run = False  # Update first_run flag after successful registration
                    logger.info(f"Reconnection successful for username='{username}'")
                    return True

                elif e.code in (
                    ExceptionCodes.DISCONNECT,
                    ExceptionCodes.INVALID_HEADER,
                    ExceptionCodes.INCOMPLETE,
                ):
                    #Transport-level failure (receive_message wraps recv-side
                    #OSErrors as DISCONNECT) — the socket is no longer
                    #trustworthy, so tear it down like the OSError path below.
                    logger.error(f"Connection to server lost during registration: {e.msg}")
                    self._teardown_server_socket()

                else:
                    logger.error(f"Registration failed with error code {e.code}: {e}")

                return False
            
            except OSError as e:
                logger.error(f"Registration failed due to socket error: {e}", exc_info=True)
                self._teardown_server_socket()
                return False

    def publish_share_data(self) -> bool:
        """ Publishes the client's share folder tree to the server.

        Called once after a successful registration, and again whenever the
        share folder changes (Phase 5 wires this to a rescan action). Returns
        True if the publish round-trip completed without a transport failure,
        False otherwise. """

        if not self.connected or not self.server_socket:
            logger.error("Cannot publish share data: client is not registered with the server.")
            return False

        share_path = Path(self.settings["share_folder_path"])

        with self.server_lock:
            try:
                #update_share_data walks share_path, msgpacks the children,
                #sends SHARE_DATA and reads the ack. It swallows RequestException
                #internally (logs only), so a recv-side failure won't surface
                #here; a send-side OSError does and is handled below.
                update_share_data(share_path, self.server_socket)
                logger.info(f"Published share data from '{share_path}' to the server.")
                return True
            except OSError as e:
                logger.error(f"Failed to publish share data due to socket error: {e}", exc_info=True)
                self._teardown_server_socket()
                return False
    
    def _heartbeat_loop(self) -> None:

        """Periodically sends heartbeat messages to the server and checks for online peers."""

        while True:
            if self.connected and self.server_socket:
                try:
                    with self.server_lock:
                        send_text(self.server_socket,HeaderCode.HEARTBEAT_REQUEST,"1")
                        reply = receive_message(self.server_socket)

                    if reply["type"] == HeaderCode.HEARTBEAT_RESPONSE:
                        # Update online peers based on the server's response
                        peer_status = msgpack.unpackb(reply["query"],use_bin_type=True)
                        self._update_online_peers(peer_status)
                except RequestException as e:
                    logger.warning(f"Heartbeat failed with RequestException: {e.msg}")

                    if e.code in (
                        ExceptionCodes.DISCONNECT,
                        ExceptionCodes.INVALID_HEADER,
                        ExceptionCodes.INCOMPLETE,
                    ):
                        logger.error("Connection to server lost during heartbeat. Tearing down socket.")
                        with self.server_lock:
                            self._teardown_server_socket()
                except OSError as e:
                    logger.error(f"Heartbeat failed due to network error: {e}", exc_info=True)
                    with self.server_lock:
                        self._teardown_server_socket()
                except Exception as e:
                    logger.error(f"Unexpected error in heartbeat loop: {e}", exc_info=True)
            time.sleep(HEARTBEAT_TIMER)



