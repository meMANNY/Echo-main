import logging
import socket
import msgpack

from utils.constants import FMT, HEADER_MSG_LEN, HEADER_TYPE_LEN
from utils.exceptions import  ExceptionCodes, RequestException
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

def receive_message(sock : socket.socket) -> SocketMessage:

    """ Function to Read a 16-byte header , 
    decode it and returns the message type and body
    Acts as a GateKeeper for incoming network traffics"""

    # Read the type byte

    try:
        type_bytes = sock.recv(HEADER_TYPE_LEN)
    
    except OSError as e:
        logging.error(msg = f"Error receiving message type: {e}")
        raise RequestException(
            msg = f"Connection error:{e}",
            code = ExceptionCodes.DISCONNECT)

    if not type_bytes:
        raise RequestException(
            msg = "Connection closed by peer",
            code = ExceptionCodes.DISCONNECT)
    
    type_char = type_bytes.decode(FMT)  


    #validate against the type header codes

    try:
        type_code = HeaderCode(type_char)
    
    except ValueError:
        logging.error(msg = f"Invalid message type in header: {type_char}")
        raise RequestException(
            msg = "Invalid message type",
            code = ExceptionCodes.INVALID_HEADER)
    
    # Read the length field

    try:

        len_bytes = recvall(sock, HEADER_MSG_LEN)
    
    except OSError as e:
        logging.error(msg = f"Error receiving message length: {e}")
        raise RequestException(
            msg = f"Connection error:{e}",
            code = ExceptionCodes.DISCONNECT)
    
    if len(len_bytes) < HEADER_MSG_LEN:
        logging.error(msg = f"Incomplete message length received: {len_bytes}")
        raise RequestException(
            msg = "Incomplete message length received",
            code = ExceptionCodes.INVALID_HEADER)

    try:
        body_len = int(len_bytes.decode(FMT).strip())
    except ValueError:

        raise RequestException(
            msg = f"Invalid message length in header: {len_bytes}",
            code = ExceptionCodes.INVALID_HEADER)
    
    # Read the message body completely using recvall

    try:
        body = recvall(sock, body_len)

    except OSError as e:
        logging.error(msg = f"Error receiving message body: {e}")
        raise RequestException(
            msg = f"Connection error:{e}",
            code = ExceptionCodes.DISCONNECT)

    if len(body) < body_len:
        raise RequestException(
            msg = "Incomplete message body received",
            code = ExceptionCodes.INCOMPLETE)
    
    # if the type is error, decode and raise the error message

    if type_code == HeaderCode.ERROR:

        try:
            err_dict = msgpack.unpackb(body)
            err = RequestException.from_dict(err_dict)

        except Exception as e:
            logging.error(msg = f"Error decoding error message body: {e}")
            raise RequestException(
                msg = "Failed to decode error message body",
                code = ExceptionCodes.BAD_REQUEST)
        
        raise err

    return SocketMessage({
        "type": type_code,
        "query": body
    })


#packs up a RequestException and sends it as an error message to the peer/client
def send_error(sock: socket.socket, err: RequestException) -> None:

    """ Function to catch error by server/peer and 
    send it to the client/peer in a structured format"""

    #when having custom class we need a default function to convert it to a dict for msgpack

    body = msgpack.packb(err, default = RequestException.to_dict,use_bin_type = True)
    send_message(sock, HeaderCode.ERROR, body)


#packs up a structured message and sends it to the peer/client


#the last two functions helps in making the business logic clean and structured.
def send_msgpack(sock: socket.socket, type_code : HeaderCode, obj) -> None:

    """ Function to send a structured message like 
    dict, list of file metadata, search results etc."""


    body = msgpack.packb(obj,use_bin_type = True)

    send_message(sock, type_code, body)


def send_text(sock: socket.socket, type_code : HeaderCode, text: str) -> None:

    """ Function to send a simple text message to the peer/client"""

    body = text.encode(FMT)
    send_message(sock, type_code, body)



    

    
