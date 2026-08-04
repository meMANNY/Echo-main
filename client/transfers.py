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

def download_file(
        core,
        owner_name: str,
        remote_file_path: str,
        filesize: int,
        resume_offset: int = 0,
        expected_hash: Optional[str] = None
)-> bool:

    """Downloads a file from a remote client via P2P connection.
    Writes to a TEMP_FOLDER_PATH , verifies the hash, and then moves to the RECV_FOLDER_PATH if successful."""

    owner_ip = core.request_peer_ip(owner_name)
    if not owner_ip:
        logger.error(f"Failed to get IP for owner {owner_name}")
        return False

    data_listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        data_listen_sock.sock.bind(("",0))
        data_listen_sock.listen(1)
        data_port = data_listen_sock.getsockname()[1]
        logger.debug(f"Data listening on port {data_port}")
    except OSError as e:
        logger.error(f"Failed to bind data socket: {e}")
        return False

    #TEMPORARY DOWNLOAD PATH
    filename = Path(remote_file_path).name
    temp_path = TEMP_FOLDER_PATH/f"{filename}.tmp"
    mode = "ab" if resume_offset > 0 else "wb" #writes data at the end of the file if resuming, otherwise overwrites

    control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_sock.settimeout(5)

    try:
        control_sock.connect((owner_ip, CLIENT_RECV_PORT))
        control_sock.settimeout(None)  # Remove timeout after connection

        #send file request to the owners
        req: FileRequest = {
            "filepath": remote_file_path,
            "port": data_port,
            "request_hash": {expected_hash is None},
            "resume_offset": resume_offset
        }
        send_msgpack(control_sock, HeaderCode.FILE_REQUEST, req)
        logger.info(f"Sent file request for {remote_file_path} to {owner_ip}:{data_port}")

        data_listen_sock.settimeout(10)  # Set a timeout for the data connection
        data_sock,addr = data_listen_sock.accept()
        data_sock.settimeout(10.0)
        logger.debug(f"Accepted data connection from {addr}")

        bytes_to_receive = filesize - resume_offset
        bytes_received = 0

        with open(temp_path,mode) as f:
            while bytes_received < bytes_to_receive:
                chunk_size = min(FILE_BUFFER_LEN, bytes_to_receive - bytes_received)
                chunk = data_sock.recv(chunk_size)
                
                if not chunk:
                    raise OSError("Connection closed by the sender before all data was received.")
                f.write(chunk)
                bytes_received += len(chunk)
                logger.debug(f"Received {bytes_received}/{bytes_to_receive} bytes")

        actual_hash = get_file_hash(str(temp_path))
        if expected_hash and actual_hash != expected_hash:
            logger.error(f"Hash mismatch for {temp_path}. Expected: {expected_hash}, Actual: {actual_hash}")
            return False

        # Move the file to the RECV_FOLDER_PATH
        final_dest = get_unique_filename(RECV_FOLDER_PATH/ filename)
        os.replace(temp_path, final_dest)
        logger.info(f"File {filename} downloaded successfully to {final_dest}")
        return True
    
    except (OSError,RequestException) as e:
        logger.error(f"Failed to connect to {owner_ip}:{CLIENT_RECV_PORT} - {e}")
        return False
    finally:
        control_sock.close()
        data_listen_sock.close()

