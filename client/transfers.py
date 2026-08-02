import logging
import os
import socket
from pathlib import Path
from typing import Optional

import msgpack

from utils.constants import (
    CLIENT_RECV_PORT,
    FILE_BUFFER_LEN,
    FMT,
    RECV_FOLDER_PATH,
    SHARE_FOLDER_PATH,
    TEMP_FOLDER_PATH,
)
from utils.exceptions import ExceptionCodes, RequestException
from utils.helpers import get_file_hash, get_unique_filename
from utils.protocol import receive_message, send_msgpack, send_text
from utils.types import FileRequest, HeaderCode

logger = logging.getLogger(__name__)