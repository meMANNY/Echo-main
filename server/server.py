from dataclasses import dataclass
import socket
import logging
import sys
import os
import time
import select

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
from utils.protocol import recvall, send_message, receive_message,send_error,send_text,send_msgpack
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






def accept_new_connection() -> None:

    """ Accepts a new client connection and adds it to the monitored sockets.

    No registration here - that happens when the client sends NEW_CONNECTION. """

    try:
        conn, addr = server_socket.accept()
        sockets_list.append(conn)
        logging.info(f"New connection accepted from {addr[0]}:{addr[1]}")

    except OSError as e:
        logging.error(f"OS error has occured for socket:{e}")
        return


def read_handler(notified_socket: socket.socket) -> None:

    """ Serves requests received from peers """

    try:
        peer_ip = notified_socket.getpeername()[0]
    except OSError :
        #if the socket is broken, cleanup and exit
        unregister_user(notified_socket)
        return
    
    try:

        request = receive_message(notified_socket)
        user = sockets_to_users.get(notified_socket)

        if user is None:

            #Gate B1: Sender is not registered
            if request["type"] != HeaderCode.NEW_CONNECTION:
                raise RequestException (
                    msg = "Unauthorised: You must register first",
                    code= ExceptionCodes.UNAUTHORIZED
                )


            username = request["query"].decode(FMT)
            logging.debug(msg=f"Registration for user {username}")
            register_user(username, peer_ip, notified_socket)
            send_message(notified_socket,HeaderCode.NEW_CONNECTION)

        else:
            #Gate B2: Sender is registered
            user.last_seen = time.time()
            logging.info(f"Dispatching message {request['type']} from user'{user.uname}'")

            match request["type"]:
                case HeaderCode.REQUEST_IP:
                    #Lookup the IP in the mapping
                    target_uname = request["query"].decode(FMT)
                    #Caller asks for his own IP
                    if target_uname == user.uname:
                        raise RequestException(
                        msg="Cannot Request your own IP",
                        code= ExceptionCodes.BAD_REQUEST)
                    
                    target_user = users.get(target_uname)
                    if not target_user:
                        raise RequestException(
                            msg=f"User '{target_uname}' is offline",
                            code= ExceptionCodes.NOT_FOUND
                        )
                    #Send IP as text response
                    send_text(notified_socket,HeaderCode.REQUEST_IP,target_user.ip)



                
                

    except RequestException as e:
        if e.code == ExceptionCodes.DISCONNECT:
            unregister_user(notified_socket)
        else:
            logging.error(f"Protocol request error from user: {e.msg}")
            send_error(notified_socket, e)
            # Drop a socket that tried to skip the registration gate so a
            # misbehaving client can't hammer us. send_error FIRST (the client
            # learns why), THEN close. Registration collisions
            # (USER_EXISTS / BAD_REQUEST) stay open so the client can retry.
            if e.code == ExceptionCodes.UNAUTHORIZED:
                unregister_user(notified_socket)


    except OSError as e:
        logging.warning(f"Connection lost ungracefully from user: {e}")
        unregister_user(notified_socket)


def cleanup() -> None:

    """ Closes every socket and the database on shutdown. """

    # iterate over a copy - unregister_user mutates sockets_list
    for sock in sockets_list.copy():
        if sock is server_socket:
            continue
        unregister_user(sock)

    server_socket.close()
    echo_db.close()
    logging.info("All sockets closed.")


def main() -> None:

    while True:
        try:
            readable, _, errored = select.select(sockets_list, [], sockets_list, 0.1)

            for notified_socket in readable:

                if notified_socket == server_socket:
                    accept_new_connection()
                else:
                    read_handler(notified_socket)

            for notified_socket in errored:
                logging.warning("OS exception occurred on socket")
                unregister_user(notified_socket)

        except KeyboardInterrupt:
            logging.info("Server shutting down....")
            cleanup()
            break


if __name__ == "__main__":
    main()
    













