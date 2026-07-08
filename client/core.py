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

class ClientCore:
    def __init__(self):

        #State properties
        self.server_socket: Optional[socket.socket] = None
        self.connected: bool = False

        self.server_lock = threading.Lock()

        #Peer tracking

        self.online_peers: dict[str, dict] = {}  # Dictionary to track online peers and their details
        self.settings: UserSettings = self.load_user_settings()
    
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
                logging.debug(f"Directory {d} created or already exists.")
            except Exception as e:
                logging.error(f"Failed to create directory {d}: {e}")
                raise RequestException(ExceptionCodes.DIRECTORY_CREATION_FAILED, f"Failed to create directory {d}: {e}")
    
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
            logging.info("User settings file not found. First Run detected. Creating default settings.")
            return default_settings
        
        try:
            with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f) #becomes a python dict
                logging.debug("User settings loaded successfully.")
                #Merge loaded keys into defaults to guard against missing properties
                return {**default_settings, **loaded}
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Failed to load user settings: {e}")
            return default_settings
    
    def save_settings(self, new_settings: UserSettings) -> None:
        """Saves the settings to dict and updated memory"""

        self.settings = new_settings
        try:
            with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
                logging.info("User settings saved successfully.")
        except OSError as e:
            logging.error(f"Failed to save user settings: {e}")
            
