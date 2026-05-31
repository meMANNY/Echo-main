
import logging
import socket
from pathlib import Path

import msgpack

from utils.constants import FMT, HEADER_MSG_LEN, HEADER_TYPE_LEN
from utils.exceptions import RequestException
from utils.helpers import path_to_dict
from utils.types import HeaderCode
from utils.protocol import send_msgpack, receive_message, send_text

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

    # share_data = msgpack.packb(path_to_dict(share_folder_path, str(share_folder_path))["children"])
    # share_data_header = f"{HeaderCode.SHARE_DATA.value}{len(share_data):<{HEADER_MSG_LEN}}".encode(FMT)
   
    # This tells Python that formatting rules are coming next.
    # <: This means left-align the value (and pad it on the right).
    # {: <15}: means format the following value to be left aligned and padded with spaces to a total width of 15 characters.


    try:
        share_dict = path_to_dict(share_folder_path, str(share_folder_path))
        children = share_dict['children']
    #Use send_msgpack for convenience wrapper
        send_msgpack(client_send_socket,HeaderCode.SHARE_DATA, children)
        msg = receive_message(client_send_socket)

        if msg["type"] != HeaderCode.SHARE_DATA:
            logging.error("Invalid Message type from the server")

    except RequestException as e:
        logging.error(f"Failed to update share data: {e.msg}")

    
def request_ip(username: str, client_send_socket: socket.socket) -> str | None:
    
    """ To request the ip address of a peer from the server using the peer's username. """   



    try:
        send_text(client_send_socket, HeaderCode.REQUEST_IP, username)
        logging.debug(f"Sent IP request for {username} to the server.")

        msg = receive_message(client_send_socket)

        logging.debug(f"Received response for IP request: {msg} for {username}.")

        if msg["type"] == HeaderCode.REQUEST_IP:
            return msg["query"].decode(FMT)
        
        else:
            logging.error(f"Unexpected message type received: {msg['type']} for IP request of {username}.")
            return None
    
    except RequestException as e:
        logging.error(f"Failed to request IP for {username}: {e.msg}")
        return None

    




        

