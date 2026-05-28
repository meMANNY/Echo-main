import logging
import socket
import msgpack

from utils.constants import FMT, HEADER_MSG_LEN, HEADER_TYPE_LEN
from utils.exceptions import ExceptionCode, RequestException
from utils.socket_functions import recvall
from utils.types import HeaderCode, SocketMessage


def build_header(type_code: HeaderCode, body_len: int) -> bytes:

    """ Build a 16 byte header 
    
    type_code == HeaderCode.*
    body_len == length of the body in bytes"""

    return f"{type_code.value}{body_len:<{HEADER_MSG_LEN}}".encode(FMT)

def send_message(sock, type_code : HeaderCode, body: bytes = b"") -> None:

    """ Function to build a header and send message via sendall

    sock == socket to send message through
    type_code == HeaderCode.*
    body == message body in bytes""" 

    
    header = build_header(type_code, len(body))
    sock.sendall(header+body)



