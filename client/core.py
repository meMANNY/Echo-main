import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Optional

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
)
from utils.exceptions import ExceptionCodes, RequestException
from utils.protocol import send_text, receive_message, send_msgpack
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
                    logger.debug("Existing server socket closed.")
                except OSError as e:
                    logger.error(f"Error closing existing server socket: {e}", exc_info=True)
                    pass

            try:
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                #Bind local port if requested or let os choose
                self.server_socket.connect((server_ip, SERVER_RECV_PORT))
                self.settings["server_ip"] = server_ip
                self.save_settings(self.settings)  # Persist the new server IP
                logger.info(f"Connected to server at {server_ip}:{SERVER_RECV_PORT}")
                return True
            except OSError as e:
                self.server_socket = None
                logger.error(f"Failed to connect to server at {server_ip}:{SERVER_RECV_PORT}: {e}", exc_info=True)
                return False
        
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
                
            except RequestException as e:
                if e.code == ExceptionCodes.USER_EXISTS:
                    logger.error(f"Registration failed: Username '{username}' already exists.")
                elif e.code == ExceptionCodes.BAD_REQUEST:
                    #reconnection
                    self.settings["uname"] = username
                    self.save_settings(self.settings)  # Persist the new username
                    self.connected = True
                    logger.info(f"Reconnection successful for username='{username}'")
                    return True
                
                else:
                    logger.error(f"Registration failed with error code {e.code}: {e}")
                
                return False
            
            except OSError as e:
                logger.error(f"Registration failed due to socket error: {e}", exc_info=True)
                self.server_socket = None
                return False

    


