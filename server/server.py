from dataclasses import dataclass
import socket
import logging
import sys
import os
import time

from datetime import datetime
from pathlib import Path
from pprint import pprint

#imports (PyPI)
import msgpack
from tinydb import TinyDB, Query

#import from utils folder

from utils.constants import FMT,HEADER_MSG_LEN,HEADER_TYPE_LEN,SERVER_RECV_PORT
from utils.exceptions import ExceptionCodes, RequestException
from utils.helpers import item_search, update_file_hash
from utils.socket_functions import get_self_ip
from utils.protocol import recvall, send_message, receive_message
from utils.types import DBData, DirData, HeaderCode, ItemSearchResult, Message, SocketMessage, UpdateHashParams

IP = get_self_ip()

# files = ['test_server.py']

echo_dir = Path.home()/ ".Echo" # Directory to store files
logs_dir = echo_dir / "logs" # Directory to store logs
db_dir = echo_dir / "db" # Path to the database file


logs_dir.mkdir(parents=True, exist_ok=True)
db_dir.mkdir(parents=True, exist_ok=True)


#Logging configuration(console and file logs)

timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
log_file = logs_dir / f"server_{timestamp}.log"

log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s') # Log Format for consistency


console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format) #allows me to watch output in real-time in console


file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(log_format) #allows me to have a record of all events in a log file



root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Set the logging level to INFO
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

#could use locks to prevent race conditions in TINYDB
echo_db = TinyDB(db_dir / "echo_db.json")

print(f"Server IP: {IP}")

server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)


#socket options -> defines socket behavior and performance

server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1) # Allow the socket to be reused immediately after the program terminates, preventing "Address already in use" errors during development and testing
server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # Disable Nagle's algorithm for low-latency communication   
server_socket.setsockopt(socket.IPPROTO_TCP, socket.IP_TOS, 0x10 | 0x08)# Set Type of Service to minimize delay (0x10 is the value for low delay)

if hasattr(socket, 'SO_PRIORITY'):
    server_socket.setsockopt(socket.SOL_SOCKET,socket.SO_PRIORITY,0x06) # Set socket priority 

server_socket.bind((IP, SERVER_RECV_PORT))
server_socket.listen(5)


#List of connected peers
sockets_list : list[socket.socket] = [server_socket]

# #To lookup IP of a given user
# uname_to_ip: dict[str,str] = {}

# #To lookup username of a given IP
# ip_to_uname: dict[str,str] = {}

# #Mapping from username to last seen timestamp
# uname_to_status: dict[str,float] = {}

#USERSTATE DATACLASS

@dataclass
class UserState:
    uname: str
    ip: str
    socket: socket.socket
    last_seen: float
#They don't hold two copies, rather two references to the same UserState object.
users: dict[str, UserState] = {} # Mapping from username to UserState

# Designing my lookups around the key I'll actually have at the moment I need them.
sockets_to_users: dict[socket.socket, UserState] = {} # Mapping from socket to UserState

def register_user(uname: str, ip: str, client_socket: socket.socket ) -> UserState:
    """Attempts to register a user state and return the UserState object.
    
    Raises RequestException if the username is already taken 
    or if the IP is already in use by another user.
    """

    if uname in users:
        existing_user = users[uname]

        if existing_user.ip == ip:
            logging.warning(f"User '{uname}' with IP {ip} is already registered.")
            raise RequestException(
                msg = "Duplicate registration attempt",
                code = ExceptionCodes.BAD_REQUEST
            )
        
        else:
            logging.warning(f"Username '{uname}' is already taken by IP {existing_user.ip}. Registration attempt from IP {ip} rejected.")
            raise RequestException(
                msg = "Username is already taken",
                code = ExceptionCodes.USER_EXISTS
            )

    user = UserState(
        uname = uname,
        ip = ip,
        socket = client_socket,
        last_seen = time.time()
    )

    users[uname] = user
    sockets_to_users[client_socket] = user

    logging.info(f"User '{uname}' successfully registered with IP {ip}.")
    return user

def unregister_user(client_socket: socket.socket) -> None:

    """ Safely cleans up and removes the user from all memory registry"""

    if client_socket == server_socket:
        logging.warning("Server tried to unregister the listener socket.")
        return

    #retrieves the value if exist or returns None.
    user = sockets_to_users.pop(client_socket, None)

    if user is not None:

        users.pop(user.uname, None)
        logging.info(f"User '{user.uname}' disconnected with IP: {user.ip}.")
    else:
        
        logging.debug("Unregistered socket connection closed.")

    #remove the closed connection
    if client_socket in sockets_list:
        sockets_list.remove(client_socket)


    try:
        client_socket.close()
    except OSError as e:
        logging.error(f"Error closing socket: {e}")














