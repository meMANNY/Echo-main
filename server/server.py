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

    logging.info(f"User '{uname}' successfully registered with Ip {ip}.")
    return user

def unregister_user(client_socket: socket.socket) -> None:

    """ Safely cleans up and removes the user from all memory registery"""

    user = sockets_to_users.pop(client_socket, None)

    if user:

        users.pop(user.uname, None)
        logging.info(f"User '{user.uname}' disconnected with IP: {user.ip}.")
    else:
        
        logging.debug("Unregistered socket connection closed.")

    try:
        client_socket.close()
    except OSError as e:
        logging.error(f"Error closing socket: {e}")















def send_file(s, server):
    try:
        file_path = input("Enter the path of the file to send: ")
        file_name = file_path.split("/")[-1]
        print("Sending file:", file_name)
        s.send(file_name.encode())

        file_size = os.path.getsize(file_path)
        file_size = str(math.ceil(file_size/1024))
        print("File size is:", file_size)

        s.send(file_size.encode()) 
        #delay 1 sec
        time.sleep(1)

        with open(file_path, 'rb') as file:
            while True:
                data = file.read(10240)
                if not data:
                    break
                s.send(data)
        print("File sent successfully")
        
    except Exception as e:
        print(f"Error sending file: {e}")

def receive_file(socket):
    file_name = socket.recv(1024)
    file_name = file_name.decode().strip()
    print("Receiving file:", file_name)

    file_size = socket.recv(1024)
    file_size = file_size.decode().strip()

    file_size = int(file_size)
    print('Blocks of file going to be received: ', file_size)

    with open(file_name, 'wb') as file:
        data = socket.recv(10240) 
        i = 0 
        while data:          
            # data = data.decode('utf-8').strip()
            file.write(data)
            data = socket.recv(10240) 

            print(i/file_size * 100, "% transfer complete")
            i += 1
    file.close()
    print("File received successfully")

# def send_message(address, socket):
#     while True:
#         message = input("-> ")
#         print("Sending: " + message)
#         socket.sendto(message.encode('utf-8'), address)

# def receive_message(socket):
#     while True:
#         data, addr = socket.recvfrom(1024)
#         data = data.decode('utf-8')
#         print("Received from: " + str(addr))
#         print("From connected user: " + data)

# function to start server and connect to 10 clients and return list of ip addresses of clients
def start_server():
    host = ''
    port = 4000

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(10)

    connected_peers = []

    for i in range(10):
        client_socket, addr = s.accept()
        connected_peers.append(peers(addr, client_socket))

    print("Server Started")

    return connected_peers


def Main():
    host = '192.168.137.1'  # Server ip
    port = 4000

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(1)

    client_socket, addr = s.accept()

    print("Server Started")

    client_address = ('192.168.137.231', 4005)  # Initialize client address
    
    # data, addr = s.recvfrom(1024)
    # client_address = addr

    send_file(s, client_address)
    # receive_file(client_socket)
    
    # receive_thread = threading.Thread(target=receive_file, args=(client_socket,))
    # send_thread = threading.Thread(target=send_file, args=(s, client_socket))

    
    # receive_thread.start()

    # send_thread.join()
    # receive_thread.join()

    # Close the socket after all files are sent
    s.close()

if __name__ == '__main__':
    Main()
