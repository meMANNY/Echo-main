import hashlib
import logging
import math
import os
from pathlib import Path

from utils.constants import HASH_BUFFER_LEN, TEMP_FOLDER_PATH   
from utils.types import CompressionMethod,DirData, ItemSearchResult, Message, TransferProgress, TransferStatus


def convert_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable format."""
    if size_bytes == 0:
        return "0B"
    
    size_name = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    i = int(math.floor(math.log(size_bytes, 1024)))

    p = math.pow(1024, i)
    s = round(size_bytes/p,2)
    return f"{s}{size_name[i]}"

def get_file_hash(filepath: str) -> str:
    """ Calculate the SHA-1 hash of a file.
    Reads the file in chunks to efficiently handle large files."""
    hash = hashlib.sha1()
    with open(filepath, "rb") as f:
        while True:
            file_bytes = f.read(HASH_BUFFER_LEN)
            hash.update(file_bytes)
            if len(file_bytes) < HASH_BUFFER_LEN:
                break
    return hash.hexdigest()


def get_unique_filename(path: Path) -> str:
    """Generate a unique filename by appending a counter if the file already exists."""
    parent,filename,extension = path.parent, path.stem, path.suffix
    counter = 1
    logging.debug(f"parent:{parent}")
    logging.debug(f"making unique file for{path}")
    while path.exists():
        path = parent / Path(filename + "_" + str(counter) + extension)
        counter += 1
    logging.debug(f"unique file is {path}")
    return str(path)

def construct_message_html(message: Message, is_self: bool)-> str:
    """Construct HTML for a message, styling the sender's name based on whether it's the current user."""
    
    return f"""
        <p style="
        margin-top:0px;
        margin-bottom:0px;
        margin-left:0px;
        margin-right:0px;
        -qt-block-indent:0;      
        text-indent:0px;
        ">
        <span style=" font-weight:600; color:{'#1a5fb4' if is_self else '#e5a50a'};">
        {"You" if is_self else message["sender"]}:
        </span>
        </p>
        """