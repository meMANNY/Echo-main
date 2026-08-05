import json
import logging
import socket
import threading
import time
import re
from pathlib import Path
from typing import Callable, Optional

import msgpack

from utils.constants import (
    CLIENT_SEND_PORT,
    FMT,
    RECV_FOLDER_PATH,
    SERVER_RECV_PORT,
    SHARE_COMPRESSED_PATH,
    SHARE_FOLDER_PATH,
    TEMP_FOLDER_PATH,
    DIRECT_TEMP_FOLDER_PATH,
    USER_SETTINGS_PATH,
    HEARTBEAT_TIMER,
    ONLINE_TIMEOUT,
    MESSAGE_MAX_LEN,
    CLIENT_RECV_PORT,
    TRANSFER_JOURNAL_PATH,
)
from utils.exceptions import ExceptionCodes, RequestException
from utils.protocol import send_text  # peer chat send; server I/O goes through ServerConnection
from utils.socket_functions import update_share_data,request_ip,request_uname
from utils.types import DirData, FileMetaData, Message, TransferProgress, UserSettings, HeaderCode, ItemSearchResult,TransferStatus, JournalEntry
from client.server_connection import ServerConnection, TRANSPORT_FAILURE_CODES




#Desktop notifications are optional: fall back to log-only if notify-py
#isn't available (headless test runs, or a python without the venv).
try:
    from notifypy import Notify
except ImportError:
    Notify = None


logger = logging.getLogger(__name__)


class ClientCore:
    def __init__(self):

        logger.info("Initializing ClientCore")

        #State properties
        # The persistent server connection owns its own socket AND the lock that
        # serializes it (Improvement A) — no method here can forget to lock, and
        # transport-failure teardown lives in one place instead of every caller.
        self.server = ServerConnection()
        self._registered: bool = False  # True only after a successful register()

        #Peer tracking
        self.online_peers: dict[str, float] = {}  # Maps online peer uname -> last_seen timestamp
        self.transfer_progress: dict[str, TransferProgress] = {}  # Maps transfer ID -> TransferProgress
        self.pause_events: dict[str, threading.Event] = {}  # Maps transfer ID -> threading.Event for pausing/resuming transfers

        # Consent policy for inbound direct transfers (4.7): return True to accept.
        # Phase 4 defaults to auto-accept (headless tests); Phase 5 assigns a
        # callback that pops an accept/reject dialog. Kept as a plain callable so
        # core stays Qt-free (same seam idea as notify()).
        self.auto_accept_transfers: bool = True
        self.on_direct_transfer_request: Optional[Callable[[FileMetaData], bool]] = None

        self.first_run: bool = False  # Flag to indicate if it's the first run of the application
        self._setup_directories()
        self.journal: dict[str,JournalEntry] = self._load_journal()
        self.settings: UserSettings = self._load_settings()
        self.message_history: dict[str, list[Message]] = {}  # In-memory message history per peer username
        self.transfer_lock = threading.Lock()  # Lock to synchronize access to transfer_progress
        self.journal_lock = threading.Lock() #guards the journal and file write
        # Start heartbeat last, once every field it might touch exists.
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop,daemon=True)
        self.heartbeat_thread.start()


        logger.info("Heartbeat thread started.")

        logger.info(
            f"ClientCore initialized (first_run={self.first_run}, "
            f"uname='{self.settings['uname']}', server_ip='{self.settings['server_ip']}')"
        )
    
    def _setup_directories(self) -> None:
        """ Idempotently creates all necessary directories for the client. """

        dirs = {
            SHARE_FOLDER_PATH,
            RECV_FOLDER_PATH,
            TEMP_FOLDER_PATH,
            SHARE_COMPRESSED_PATH,
            DIRECT_TEMP_FOLDER_PATH,
            USER_SETTINGS_PATH.parent, #~/.Echo/db/
        }

        for d in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Directory {d} created or already exists.")
            except Exception as e:
                logger.error(f"Failed to create directory {d}: {e}", exc_info=True)
                
    
    def _load_settings(self) -> UserSettings:
        """ Loads user settings from a JSON file. If the file doesn't exist, it creates default settings. """

        default_settings: UserSettings = {
            "uname": "",
            "share_folder_path": str(SHARE_FOLDER_PATH),
            "server_ip": "127.0.0.1",
            "downloads_folder_path": str(RECV_FOLDER_PATH),
            "show_notifications": True,
        }

        if not USER_SETTINGS_PATH.exists():
            logger.info("User settings file not found. First Run detected. Creating default settings.")
            self.first_run = True
            return default_settings

        try:
            with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f) #becomes a python dict

                #Guard against valid JSON that isn't an object (e.g. [], 42, "x")
                #before calling .get() / merging, otherwise those would raise.
                if not isinstance(loaded, dict):
                    logger.error("User settings file is not a valid dictionary. Treating as first run.")
                    self.first_run = True
                    return default_settings

                #File exists but no username.
                if not loaded.get("uname"):
                    logger.info("User settings file found but username is empty. First Run detected.")
                    self.first_run = True
                else:
                    self.first_run = False

                logger.debug("User settings loaded successfully.")
                #Merge loaded settings with defaults to ensure all keys are present
                return {**default_settings, **loaded}

        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load user settings: {e}. Treating as first run.", exc_info=True)
            self.first_run = True
            return default_settings
    
    def save_settings(self, new_settings: UserSettings) -> None:
        """Saves the settings to dict and updated memory"""

        self.settings = new_settings
        logger.debug(f"Persisting settings for uname='{new_settings['uname']}'")
        try:
            with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
                logger.info("User settings saved successfully.")
        except OSError as e:
            logger.error(f"Failed to save user settings: {e}")
    
    @property
    def connected(self) -> bool:
        """Registered AND the socket is still open. Because ServerConnection
        clears the socket on a transport failure, this reflects reality without
        a separate flag to keep in sync — the old manual self.connected=False
        scattered through every error path is gone."""
        return self._registered and self.server.is_open

    def connect_to_server(self,server_ip: str) -> bool:
        """ Establish a socket connection to the central server. Returns True on
        success. The socket + its lock live in ServerConnection now. """
        # A fresh socket is unregistered until register() runs on it.
        self._registered = False
        if not self.server.connect(server_ip):
            return False
        self.settings["server_ip"] = server_ip
        self.save_settings(self.settings)  # Persist the new server IP
        return True

    def register(self, username: str) -> bool:
        """ Performs the new connection registration with the server. Returns True if successful, False otherwise. """

        if not self.server.is_open:
            logger.error("Cannot register: No server connection established.")
            return False

        try:
            response = self.server.request(HeaderCode.NEW_CONNECTION, username)

            if response["type"] == HeaderCode.NEW_CONNECTION:
                logger.info(f"Registration successful for username='{username}'")
                self.settings["uname"] = username
                self.save_settings(self.settings)  # Persist the new username
                self._registered = True
                self.first_run = False  # Update first_run flag after successful registration
                return True

            logger.error(f"Unexpected response from server during registration: {response}")
            return False

        except RequestException as e:
            if e.code == ExceptionCodes.USER_EXISTS:
                logger.warning(f"Registration failed: Username '{username}' already taken by another host.")
            elif e.code == ExceptionCodes.BAD_REQUEST:
                #reconnection (same host re-registering) — socket is still good.
                self.settings["uname"] = username
                self.save_settings(self.settings)  # Persist the new username
                self._registered = True
                self.first_run = False  # Update first_run flag after successful registration
                logger.info(f"Reconnection successful for username='{username}'")
                return True
            elif e.code in TRANSPORT_FAILURE_CODES:
                #ServerConnection already tore the socket down; connected is now False.
                logger.error(f"Connection to server lost during registration: {e.msg}")
            else:
                logger.error(f"Registration failed with error code {e.code}: {e}")
            return False

        except OSError as e:
            #ServerConnection already tore the socket down.
            logger.error(f"Registration failed due to socket error: {e}", exc_info=True)
            return False

    def publish_share_data(self) -> bool:
        """ Publishes the client's share folder tree to the server.

        Called once after a successful registration, and again whenever the
        share folder changes (Phase 5 wires this to a rescan action). Returns
        True if the publish round-trip completed without a transport failure,
        False otherwise. """

        if not self.connected:
            logger.error("Cannot publish share data: client is not registered with the server.")
            return False

        share_path = Path(self.settings["share_folder_path"])

        try:
            # update_share_data walks share_path, msgpacks the children, sends
            # SHARE_DATA and reads the ack — a multi-step exchange, so it runs
            # under the lock via server.run(). Transport failures tear the
            # socket down there and surface here as OSError/RequestException.
            self.server.run(lambda sock: update_share_data(share_path, sock))
            logger.info(f"Published share data from '{share_path}' to the server.")
            return True
        except (OSError, RequestException) as e:
            logger.error(f"Failed to publish share data due to socket error: {e}", exc_info=True)
            return False
    
    def _heartbeat_loop(self) -> None:

        """Periodically sends heartbeat messages to the server and checks for online peers."""

        while True:
            if self.connected:
                try:
                    reply = self.server.request(HeaderCode.HEARTBEAT_REQUEST, "1")
                    if reply["type"] == HeaderCode.HEARTBEAT_REQUEST:
                        # Update online peers based on the server's response
                        peer_status = msgpack.unpackb(reply["query"], raw=False)  # Assuming the server sends a msgpack-encoded dict
                        self._update_online_peers(peer_status)
                except RequestException as e:
                    if e.code in TRANSPORT_FAILURE_CODES:
                        #ServerConnection already tore the socket down; connected flips False.
                        logger.error(f"Connection to server lost during heartbeat ({e.code.name}): {e.msg}.")
                    else:
                        logger.warning(f"Heartbeat failed with RequestException: {e.msg}")
                except OSError as e:
                    #ServerConnection already tore the socket down.
                    logger.error(f"Heartbeat failed due to network error: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"Unexpected error in heartbeat loop: {e}", exc_info=True)
            time.sleep(HEARTBEAT_TIMER)
    
    def _update_online_peers(self, peer_status: dict) -> None:

        """Filters the online peers based on the last heartbeat timestamp and updates the online_peers dictionary."""

        now = time.time()
        active = {} # Temporary dictionary to hold active peers

        for username, last_seen in peer_status.items():
            # If the last seen timestamp is within the ONLINE_TIMEOUT, consider the peer active
            if now - last_seen < ONLINE_TIMEOUT:
                active[username] = last_seen

        # Update the online_peers dictionary with the active peers
        self.online_peers = active
        logger.debug(f"Updated online peers: {list(self.online_peers.keys())}")

    def request_peer_ip(self,username: str) -> Optional[str]:
        """ Queries the server for the IP address of a given peer username. Returns the IP address as a string if found, or None if not found or on error. """

        if not self.connected:
            logger.error("Cannot request peer IP: client is not registered with the server.")
            return None
        try:
            ip = self.server.run(lambda sock: request_ip(username, sock))
            logger.info(f"Received IP for peer '{username}': {ip}")
            return ip
        except (OSError, RequestException) as e:
            #request_ip already swallows semantic errors; this only catches a raw
            #transport failure (which server.run has torn the socket down for).
            logger.error(f"Failed to request IP for '{username}': {e}")
            return None

    def request_peer_uname(self,ip: str) -> Optional[str]:
        """ Queries the server for the username of a given peer IP address. Returns the username as a string if found, or None if not found or on error. """

        if not self.connected:
            logger.error("Cannot request peer username: client is not registered with the server.")
            return None
        try:
            uname = self.server.run(lambda sock: request_uname(ip, sock))
            logger.info(f"Received username for IP '{ip}': {uname}")
            return uname
        except (OSError, RequestException) as e:
            logger.error(f"Failed to request username for IP '{ip}': {e}")
            return None
    
    def add_message_to_history(self,peer_uname: str,content: str,is_self: bool) -> None:
        """ Appends a message to the in-memory message history for a given peer. """

        sender = self.settings["uname"] if is_self else peer_uname
        #setdefault is atomic: both a sender thread and a peer-handler thread
        #get the SAME list back, so concurrent first messages can't clobber
        #each other (a separate check-then-set would race).
        self.message_history.setdefault(peer_uname, []).append({
            "sender": sender,
            "content": content
        })

        logger.debug(f"Message history updated for {peer_uname}. Total messages: {len(self.message_history[peer_uname])}")


    def send_chat_message(self,target_uname: str,text: str) -> bool:
        """ Sends a chat message to a peer identified by their username. """

        text_bytes = text.encode(FMT)
        if len(text_bytes) > MESSAGE_MAX_LEN:
            logger.error(f"Message exceeds maximum length of {MESSAGE_MAX_LEN} bytes.")
            return False
    
        target_ip = self.request_peer_ip(target_uname)
        if not target_ip:
            logger.error(f"Could not find IP for username {target_uname}.")
            return False

        peer_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        PEER_CONNECTION_TIMEOUT = 3.0  # seconds
        peer_sock.settimeout(PEER_CONNECTION_TIMEOUT)

        try:
            logger.debug(f"Attempting to connect to {target_uname} at {target_ip}:{CLIENT_RECV_PORT}.")
            peer_sock.connect((target_ip, CLIENT_RECV_PORT))

            peer_sock.settimeout(None)  # Remove timeout after connection is established
            send_text(peer_sock,HeaderCode.MESSAGE,text)

            self.add_message_to_history(target_uname,text,is_self=True)
            logger.info(f"Sent message to {target_uname}: {text}")
            return True
        except (socket.timeout,OSError) as e:
            logger.error(f"Failed to send message to {target_uname} at {target_ip}: {e}")
            return False
        finally:
            peer_sock.close()

    def notify(self, title: str, body: str) -> None:
        """ Thin notification wrapper (Step 4.2.4).

        Honors settings["show_notifications"]. Fires a desktop notification
        via notify-py when available, and always logs. Phase 5 replaces this
        with a GUI route (signal/toast) — callers just use notify(title, body).
        Never raises: a notification failure must not crash the caller
        (typically a peer-listener worker thread). """

        if not self.settings.get("show_notifications", True):
            logger.debug(f"Notification suppressed (show_notifications off): {title}")
            return

        try:
            if Notify is not None:
                notification = Notify()
                notification.application_name = "Echo"
                notification.title = title
                notification.message = body
                #block=False: don't stall the calling thread on the OS call
                notification.send(block=False)
            logger.info(f"[NOTIFY] {title}: {body}")
        except Exception as e:
            #Notification failures are cosmetic — log and move on.
            logger.error(f"Failed to send desktop notification: {e}")
    
    def browse(self,target_uname: str) -> Optional[list[DirData]]:

        """ Fetches the shared file directory tree for a target user.
        Returns a list of DirData objects representing the directory tree, or None on failure. """

        if not self.connected:
            logger.error("Cannot browse: client is not registered with the server.")
            return None

        try:
            reply = self.server.request(HeaderCode.FILE_BROWSE, target_uname)
            if reply["type"] == HeaderCode.FILE_BROWSE:
                #Unpack record
                doc = msgpack.unpackb(reply["query"], raw=False)  # Assuming the server sends a msgpack-encoded list of DirData
                share_tree = doc.get("share", [])
                logger.info(f"Received share data for {target_uname}. Total items: {len(share_tree)}")
                return share_tree

            logger.error(f"Unexpected response from server during browse: {reply}")
            return None

        except RequestException as e:
            if e.code == ExceptionCodes.NOT_FOUND:
                logger.warning(f"Browse failed: User '{target_uname}' not found on server.")
            elif e.code in TRANSPORT_FAILURE_CODES:
                #ServerConnection already tore the socket down.
                logger.error(f"Connection to server lost during browse ({e.code.name}): {e.msg}.")
            else:
                logger.error(f"Browse failed with error code {e.code}: {e}")
            return None
        except OSError as e:
            logger.error(f"Browse failed due to socket error: {e}", exc_info=True)
            return None
    
    def search(self,query: str) -> Optional[list[ItemSearchResult]]:

        """ Searches the shares of all online peers for items matching the query string."""

        if not self.connected:
            logger.error("Cannot search: client is not registered with the server.")
            return None

        clean_query = query.strip().lower()
        if not clean_query:
            logger.warning("Search query is empty after stripping whitespace.")
            return []

        #Escape client side regex errors.
        #Can cause client side error , to be handled gracefully.
        safe_query = re.escape(clean_query)

        try:
            reply = self.server.request(HeaderCode.FILE_SEARCH, safe_query)
            if reply["type"] == HeaderCode.FILE_SEARCH:
                results = msgpack.unpackb(reply["query"], raw=False)  # Assuming the server sends a msgpack-encoded list of ItemSearchResult
                logger.info(f"Received search results for query '{query}'. Total items found: {len(results)}")
                return results

            logger.error(f"Unexpected response from server during search: {reply}")
            return None
        except RequestException as e:
            if e.code in TRANSPORT_FAILURE_CODES:
                #ServerConnection already tore the socket down.
                logger.error(f"Connection to server lost during search ({e.code.name}): {e.msg}.")
            else:
                logger.error(f"Search failed with error code {e.code}: {e}")
            return None
        except OSError as e:
            logger.error(f"Search failed due to socket error: {e}", exc_info=True)
            return None

    def updated_file_hash_on_server(self,filepath: str,file_hash: str) -> bool:

        """Pushes a newly calculated file hash to the server for a given relative file path. This is used when a peer requests a file hash that the server does not have cached."""
        if not self.connected:
            return False

        payload = {
            "filepath": filepath,
            "hash": file_hash
        }
        try:
            reply = self.server.request(HeaderCode.UPDATE_HASH, payload, msgpack_body=True)
            return reply["type"] == HeaderCode.UPDATE_HASH
        except (RequestException, OSError) as e:
            logger.error(f"Failed to update file hash on server for '{filepath}': {e}", exc_info=True)
            return False
    @staticmethod
    def _percent(received: int, total: int) -> float:
        return (received / total * 100) if total > 0 else 100.0

    def start_transfer(self,key: str,total: int, received: int = 0) -> None:
        """Register a new (or resumed) download. received is non-zero on resume"""

        with self.transfer_lock:
            event  = threading.Event()
            event.set()  # Start in unpaused state
            self.pause_events[key] = event
            self.transfer_progress[key] = {
                "status": TransferStatus.DOWNLOADING,
                "progress": received,
                "total": total,
                "percent_progress": self._percent(received, total)
            }


    def update_transfer(self,key:str,received: int) -> None:
        """Update bytes-received for an active transfer(called each chunk)."""

        with self.transfer_lock:
            rec = self.transfer_progress.get(key)
            if rec is None:
                logger.warning(f"Attempted to update non-existent transfer '{key}'.")
                return
            rec["progress"] = received
            rec["percent_progress"] = self._percent(received, rec["total"])

    def set_transfer_status(self,key: str, status: TransferStatus) -> None:
        """Update the status of an active transfer."""

        with self.transfer_lock:
            rec = self.transfer_progress.get(key)
            if rec is None:
                logger.warning(f"Attempted to set status for non-existent transfer '{key}'.")
                return
            rec["status"] = status
        if status == TransferStatus.COMPLETED:
            self.journal_remove(key)
        else:
            self.journal_update_status(key, status)



    def get_transfer(self,key: str) -> Optional[TransferProgress]:
        """Return a SNAPSHOT copy so the ui reads a consistent view lock-free."""

        with self.transfer_lock:
            rec = self.transfer_progress.get(key)
            return dict(rec) if rec is not None else None  # Return a copy to prevent external mutation

    def get_active_transfers(self) -> dict[str, TransferProgress]:
        """Return a SNAPSHOT copy of all active transfers so the ui reads a consistent view lock-free."""

        with self.transfer_lock:
            return {k: dict(v) for k, v in self.transfer_progress.items()}  # Return copies to prevent external mutation

    def remove_transfer(self,key: str) -> None:
        with self.transfer_lock:
            self.transfer_progress.pop(key, None)  # Remove the transfer if it exists, do nothing otherwise


    def pause_transfer(self, key: str) -> bool:
        with self.transfer_lock:
            event = self.pause_events.get(key)
            if event is None:
                return False
            event.clear()
            rec = self.transfer_progress.get(key)
            if rec is not None:
                rec["status"] = TransferStatus.PAUSED
        self.journal_update_status(key, TransferStatus.PAUSED)   # after the lock
        logger.info(f"Transfer '{key}' paused.")
        return True


    def is_transfer_paused(self,key: str) -> bool:
        """Check if an active transfer is paused"""

        with self.transfer_lock:
            event = self.pause_events.get(key)
            return event is not None and not event.is_set()

    
    def _load_journal(self) -> dict[str, "JournalEntry"]:
        if not TRANSFER_JOURNAL_PATH.exists():
            return {}
        try:
            with open(TRANSFER_JOURNAL_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load transfer journal: {e}. Starting with an empty journal.")
            return {}

    def _save_journal(self) -> None:
        """Atomically rewrite the journal. Caller MUST hold journal_lock."""
        tmp = TRANSFER_JOURNAL_PATH.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.journal, f, indent=2)
            tmp.replace(TRANSFER_JOURNAL_PATH)     # atomic on the same filesystem
        except OSError as e:
            logger.error(f"Failed to persist transfer journal: {e}")

    def journal_start(self, key: str, uname: str, filepath: str, total: int,
                    received: int, expected_hash: Optional[str],
                    dest_subpath: Optional[str]) -> None:
        """Record a download as in-flight so it survives a crash/restart."""
        with self.journal_lock:
            self.journal[key] = {
                "uname": uname, "filepath": filepath, "total": total,
                "received": received, "status": TransferStatus.DOWNLOADING.value,
                "hash": expected_hash, "dest_subpath": dest_subpath,
            }
            self._save_journal()

    def journal_update_status(self, key: str, status: TransferStatus) -> None:
        with self.journal_lock:
            entry = self.journal.get(key)
            if entry is None:
                return
            entry["status"] = status.value
            self._save_journal()

    def journal_remove(self, key: str) -> None:
        with self.journal_lock:
            if self.journal.pop(key, None) is not None:
                self._save_journal()

    def get_resumable_transfers(self) -> list["JournalEntry"]:
        """Entries left DOWNLOADING (hard-killed) or PAUSED by a previous session."""
        resumable = (TransferStatus.DOWNLOADING.value, TransferStatus.PAUSED.value)
        with self.journal_lock:
            return [dict(e) for e in self.journal.values() if e["status"] in resumable]

    def should_accept_transfer(self, metadata: FileMetaData) -> bool:
        """Consent decision for an inbound direct transfer (4.7.1).

        Uses on_direct_transfer_request if set (Phase 5 dialog), else falls back
        to the auto_accept_transfers flag (Phase 4 headless default). A broken
        consent callback must not crash the listener thread — it rejects."""
        if self.on_direct_transfer_request is not None:
            try:
                return bool(self.on_direct_transfer_request(metadata))
            except Exception as e:
                logger.error(f"Direct-transfer consent callback failed: {e}; rejecting.")
                return False
        return self.auto_accept_transfers

            




