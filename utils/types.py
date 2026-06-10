from enum import Enum

from typing import NamedTuple, TypedDict


class HeaderCode(Enum):
    """Header codes for different message types"""
    
    ERROR = "e"
    DIRECT_TRANSFER_REQUEST = "t"
    DIRECT_TRANSFER = "T"
    FILE_REQUEST = "F"
    FILE_BROWSE = "b"
    FILE_SEARCH = "s"
    HEARTBEAT_REQUEST = "H"
    REQUEST_UNAME = "R"
    MESSAGE = "m"
    NEW_CONNECTION = "n"
    REQUEST_IP = "r"
    SHARE_DATA = "d"
    UPDATE_HASH = "h"
    UPDATE_SHARE_DATA = "D"

class TransferStatus(Enum):
    NEVER_STARTED = 0
    DOWNLOADING = 1
    PAUSED = 2
    COMPLETED = 3
    FAILED = 4

class CompressionMethod(Enum):
    NONE = 0
    ZSTD = 1

class SocketMessage(TypedDict):
    type: HeaderCode
    query: bytes

class TransferProgress(TypedDict):
    status: TransferStatus
    progress: int
    percent_progress: float

class ProgressBarData(TypedDict):
    current: int
    total: int

class FileMetaData(TypedDict):
    path: str
    size: int
    hash: str | None
    compression: CompressionMethod
    
class FileRequest(TypedDict):
    filepath: str
    port: int
    request_hash: bool
    resume_offset: int      #if part of file has been received previously

class FileSearchResult(TypedDict):
    uname: str
    filepath: str
    filesize: int
    hash: str | None

class DirData(TypedDict):
    name: str
    path: str
    type: str
    size: int | None
    hash: str | None
    compression: int
    children: list["DirData"] | None

class ItemSearchResult(TypedDict):
    owner: str
    data: DirData

class UpdateHashParams(TypedDict):
    filepath: str
    hash: str
    
class DBData(TypedDict):
    uname: str
    share: list[DirData]

class UserSettings(TypedDict):
    uname: str
    share_folder_path: str 
    server_ip: str
    downloads_folder_path: str 
    show_notifications: bool

class Message(TypedDict):
    sender: str
    content: str
    

    

    



    