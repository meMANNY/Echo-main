
import logging
import socket
from pathlib import Path

import msgpack

from utils.constants import FMT, HEADER_MSG_LEN, HEADER_TYPE_LEN
from utils.exceptions import RequestException
from utils.helpers import path_to_dict
from utils.types import HeaderCode


def get_self_ip()->str:

    """ To obtain the current user's IP address"""

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)

    try:
        s.connect(("1.1.1.1",1)) #public address
        ip = s.getsockname()[0]

    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    
    return ip 
    

def recvall(peer_socket: socket.socket, length: int) -> bytes:

    """ To ensure loseless data receipt in large communication"""

    received = 0
    data:bytes = b""

    while received != length:
        chunk = peer_socket.recv(length)
        if not len(chunk):
            break
        data += chunk
        received += len(chunk)
    
    return data

def update_share_data(share_folder_path: Path, client_send_socket: socket.socket):

    """ To send new share folder data to the server. 

    Dictonary representation is sent to the server """
    #[ 1 byte: HeaderCode ][ 15 bytes: length, left-padded ][ N bytes: body ]

    share_data = msgpack.packb(path_to_dict(share_folder_path, str(share_folder_path))["children"])
    share_data_header = f"{HeaderCode.SHARE_DATA.value}{len(share_data):<{HEADER_MSG_LEN}}".encode(FMT)
   
    client_send_socket.sendall(share_data_header+share_data)

    msg_type = client_send_socket.recv(HEADER_TYPE_LEN).decode(FMT)
    if msg_type != HeaderCode.SHARE_DATA.value:
        logging.error("Invalid message type from the server.")

    
    
    




        

