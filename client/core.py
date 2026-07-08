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


