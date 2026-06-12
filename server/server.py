from dataclasses import dataclass
import socket
import logging
import sys
import os
import time
import select

from datetime import datetime
from pathlib import Path
from pprint import pformat

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

def validate_share_tree(tree: list) -> None:
    """ Recursively validates the share directory tree for:
    1. Structure (must be list/dict).
    2. Path Traversal or Absolute Path vulnerability (Security Guard).
    3. File sizes out of bounds (int64).
    Raises RequestException if any validation fails."""

    if not isinstance (tree, list):
        raise RequestException(
            msg="Invalid share structure: root must be a list",
            code=ExceptionCodes.BAD_REQUEST
        )

    for item in tree:
        #Guard: Every entry must be a dict
        if not isinstance(item,dict):
            raise RequestException(
                msg="Invalid share item: entries must be a dict",
                code=ExceptionCodes.BAD_REQUEST
            )
        path = item.get("path")
        item_type = item.get("type")

        #Path Validation
        if not path or not isinstance(path,str):
            raise RequestException(
                msg="Malformed share item: missing or invalid path string",
                code=ExceptionCodes.BAD_REQUEST
            )
        #is_absolute = full path from drive root
        if Path(path).is_absolute() or ".." in path:
            raise RequestException(
                msg="Security Warning: absolute path detected",
                code=ExceptionCodes.BAD_REQUEST
            )
        

        #3 File Size Validation
        if item_type == "file":
            size = item.get("size")

            #size must fit in 64 bit-int
            if not isinstance(size,int) or size < 0 or size >= 2**63:
                raise RequestException( 
                    msg=f"Invalid file size for item: {path}",
                    code=ExceptionCodes.BAD_REQUEST
                )
            
        elif item_type == "directory":
            children = item.get("children",[])
            #recursive check
            validate_share_tree(children)
        
        else: 
            raise RequestException(
                msg= f"Unknown file type: {item_type}",
                code=ExceptionCodes.BAD_REQUEST
            )


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
                        msg="Cannot request your own IP",
                        code= ExceptionCodes.BAD_REQUEST)
                    
                    target_user = users.get(target_uname)
                    if not target_user:
                        raise RequestException(
                            msg=f"User '{target_uname}' is offline",
                            code= ExceptionCodes.NOT_FOUND
                        )
                    #Send IP as text response
                    send_text(notified_socket,HeaderCode.REQUEST_IP,target_user.ip)

                #Get a peer's username from their IP
                case HeaderCode.REQUEST_UNAME:
                    
                    target_ip = request["query"].decode(FMT)

                    #Caller asks for his own username
                    if target_ip == user.ip:
                        raise RequestException(
                            msg="Cannot request your own username",
                            code= ExceptionCodes.BAD_REQUEST
                        )

                    #O(n) scan over users dict to find matching IP
                    target_user = None

                    for u in users.values():
                        if u.ip == target_ip:
                            target_user = u
                            break
                    
                    if not target_user:
                        raise RequestException(
                            msg= f"IP address {target_ip} is not registered",
                            code=ExceptionCodes.NOT_FOUND
                        )
                    #Send username as a text response
                    send_text(notified_socket,HeaderCode.REQUEST_UNAME,target_user.uname)


                case HeaderCode.HEARTBEAT_REQUEST:
                    #Update the user's online status

                    #Build up the dictionary of all the other user last seen time.
                    heartbeat = {
                        other_uname: other_user.last_seen for other_uname,other_user in users.items() if other_uname != user.uname
                    }

                    #Send the dict of active user
                    send_msgpack(notified_socket,HeaderCode.HEARTBEAT_REQUEST,heartbeat)
                
                case HeaderCode.SHARE_DATA:
                    #Sending share data of the user

                    #Implementing new logic: DoS Guard -> reject a package if the payload is huge!
                    MAX_PAYLOAD_SIZE = 10*1024*1024 #10Mb
                    if len(request["query"]) > MAX_PAYLOAD_SIZE:
                        raise RequestException(
                            msg="Share data payload size exceeds size limits.",
                            code=ExceptionCodes.BAD_REQUEST
                        )
                    
                    #unpacking the list of "children" from msgpack bytes
                    try:
                        share_data = msgpack.unpackb(request["query"])
                    except Exception as e:
                        logging.warning(f"Failed to unpack msg payload from '{user.uname}': {e}")
                        raise RequestException(
                            msg="Malformed message payload",
                            code=ExceptionCodes.BAD_REQUEST
                        )


                    #Calling the new logic function here
                    validate_share_tree(share_data)

                    Userquery = Query()
                    logging.debug(f"Received update to share data for user {user.uname}")
                    #Update the share data under the username key if it exists
                    #or insert it if it does not exist
                    echo_db.upsert({"uname": user.uname,"share": share_data}, Userquery.uname == user.uname)

                    #Acknowledge only (empty body)
                    send_message(notified_socket,HeaderCode.SHARE_DATA)

                
                case HeaderCode.FILE_SEARCH:
                    #Fuzzy search for files across the network.

                    search_qrt = request["query"].decode(FMT).lower()
                    all_results = []

                    #Iterating over all user records
                    for doc in echo_db.all():
                        if doc["uname"] == user.uname:
                            continue
                        
                        results = []
                        item_search(doc["share"],results,search_qrt,doc["uname"])
                        all_results.extend(results)

                    logging.debug(f"{pformat(all_results)}")
                    
                    #Send back the search result list back to the user.
                    send_msgpack(notified_socket,HeaderCode.FILE_SEARCH,all_results)

                case HeaderCode.FILE_BROWSE:
                    #Retrieve the shared data of a user.
                    target_uname = request["query"].decode(FMT)

                    #Distinguish "user doesn't exist" from "user exists but
                    #hasn't uploaded share data yet". Only an unknown user is
                    #a NOT_FOUND; a known user with no data browses as empty.
                    if target_uname not in users:
                        raise RequestException(
                            msg=f"User '{target_uname}' is not online",
                            code=ExceptionCodes.NOT_FOUND
                        )

                    Userquery = Query()
                    records: list[DirData] = echo_db.search(Userquery.uname == target_uname)

                    #Registered but no share row yet -> return an empty share
                    #gracefully, keeping the same response shape as a hit.
                    if not records:
                        send_msgpack(notified_socket,HeaderCode.FILE_BROWSE,{"uname": target_uname, "share": []})
                    else:
                        #Send the share data packed in msgpack
                        send_msgpack(notified_socket,HeaderCode.FILE_BROWSE,records[0])

                case HeaderCode.UPDATE_HASH:
                    #Updating the hash of a file item
                    """
                    When a client first scans its share folder, it doesn't compute
                    hashes (would be slow). It sends share data with hash=None.
                    - Hashes are computed lazily when someone tries to download.
                    - The downloader gets the hash, the uploader's server-side record
                        is updated so future downloaders don't have to recompute."""



                    try:

                        params = msgpack.unpackb(request["query"])
                        filepath = params["filepath"]
                        new_hash = params["hash"]
                    except Exception as e:
                        logging.warning(f"Failed to unpack update_hash payload from {user.uname}")
                        raise RequestException(
                            msg="Malformed update_hash payload",
                            code=ExceptionCodes.BAD_REQUEST
                        )

                    Userquery = Query()
                    record = echo_db.search(Userquery.uname == user.uname)

                    if not record:
                        raise RequestException(
                            msg="No share directory registered.",
                            code=ExceptionCodes.NOT_FOUND
                        )
                    
                    share = record[0]["share"]

                    #Mutate the tree in-place
                    update_file_hash(share,filepath,new_hash)

                    echo_db.update({"share": share},Userquery.uname == user.uname)

                    send_message(notified_socket,HeaderCode.UPDATE_HASH)

                case HeaderCode.ERROR:
                    #Client sent an error
                    try:
                        exc_dict = msgpack.unpackb(request["query"])
                        logging.warning(f"Error reported by client '{user.uname}': {exc_dict.get('msg')}")
                    except Exception as e:
                        #Not raising exception as the server will send error packet back to client causing a loop.
                        logging.warning("Received malformed error payload")

                case _:
                    logging.warning(f"bad request from user.")
                    raise RequestException(
                        msg=f"Bad request from {user.uname}",
                        code=ExceptionCodes.BAD_REQUEST
                    )

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
    













