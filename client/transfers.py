import logging
import os
import socket
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import msgpack

from utils.constants import (
    CLIENT_RECV_PORT,
    FILE_BUFFER_LEN,
    FMT,
    SHARE_FOLDER_PATH,
    TEMP_FOLDER_PATH,
)
from utils.exceptions import ExceptionCodes, RequestException
from utils.helpers import get_file_hash, get_files_in_dir, get_unique_filename
from utils.protocol import receive_message, send_message, send_msgpack, send_text,send_error
from utils.types import CompressionMethod, DirData, FileMetaData, FileRequest, HeaderCode, TransferStatus

#Import only for type hints: a runtime import of client.core would create a
#circular dependency once ClientCore instantiates PeerListener -> transfers.
if TYPE_CHECKING:
    from client.core import ClientCore


logger = logging.getLogger(__name__)


def _transfer_key(owner_name: str, remote_file_path: str) -> str:
    """Stable key for a transfer's progress + pause state (owner + share path)."""
    return f"{owner_name}:{remote_file_path}"


def _temp_path_for(owner_name: str, remote_file_path: str) -> Path:
    """The partial-download file for a transfer. MUST match what download_file
    writes to, so resume can measure how much was already received."""
    return TEMP_FOLDER_PATH / owner_name / (remote_file_path + ".tmp")


def download_file(
        core: "ClientCore",
        owner_name: str,
        remote_file_path: str,
        filesize: int,
        resume_offset: int = 0,
        expected_hash: Optional[str] = None,
        dest_subpath: Optional[str] = None,
)-> bool:

    """Downloads a file from a remote client via P2P connection.
    Writes to a temp file, verifies the hash, and then moves it into the
    downloads folder if successful.

    dest_subpath: path relative to the downloads folder where the file should
    land. Folder downloads pass the file's share-relative path so the source
    tree is mirrored; a single-file download leaves it None and the file lands
    flat under its own name."""
    transfer_key = _transfer_key(owner_name, remote_file_path)
    core.start_transfer(transfer_key,total = filesize,received = resume_offset)
    core.journal_start(transfer_key,owner_name,remote_file_path,filesize,resume_offset,expected_hash,dest_subpath)
    owner_ip = core.request_peer_ip(owner_name)
    if not owner_ip:
        logger.error(f"Failed to get IP for owner {owner_name}")
        core.set_transfer_status(transfer_key, TransferStatus.FAILED)
        return False

    data_listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        data_listen_sock.bind(("",0))
        data_listen_sock.listen(1)
        data_port = data_listen_sock.getsockname()[1]
        logger.debug(f"Data listening on port {data_port}")
    except OSError as e:
        logger.error(f"Failed to bind data socket: {e}")
        core.set_transfer_status(transfer_key, TransferStatus.FAILED)
        data_listen_sock.close()
        return False

    # Destination: mirror the source tree for a folder download (dest_subpath
    # given), else land the file flat under its own name for a single file.
    final_rel = Path(dest_subpath) if dest_subpath else Path(Path(remote_file_path).name)
    downloads_root = Path(core.settings["downloads_folder_path"])

    # Temp-first: a download interrupted halfway must not leave a corrupt file
    # masquerading as complete in Downloads — the temp file IS the resume state.
    # Namespace it by owner + share-relative path so two files that share a
    # basename (a/readme.txt vs b/readme.txt) never collide in temp.
    temp_path = _temp_path_for(owner_name, remote_file_path)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ab" if resume_offset > 0 else "wb" #append when resuming, else overwrite

    control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_sock.settimeout(5)

    try:
        control_sock.connect((owner_ip, CLIENT_RECV_PORT))
        control_sock.settimeout(None)  # Remove timeout after connection

        #send file request to the owners
        req: FileRequest = {
            "filepath": remote_file_path,
            "port": data_port,
            "request_hash": (expected_hash is None),
            "resume_offset": resume_offset
        }
        send_msgpack(control_sock, HeaderCode.FILE_REQUEST, req)
        logger.info(f"Sent file request for {remote_file_path} to {owner_ip}:{data_port}")

        data_listen_sock.settimeout(10)  # Set a timeout for the data connection
        data_sock,addr = data_listen_sock.accept()
        data_sock.settimeout(10.0)
        logger.debug(f"Accepted data connection from {addr}")

        bytes_to_receive = filesize - resume_offset
        bytes_received = 0
        try:
            with open(temp_path,mode) as f:
                while bytes_received < bytes_to_receive:
                    if core.is_transfer_paused(transfer_key):
                        logger.info(f"Transfer {transfer_key} paused; exiting (resume re-issues the request). Partial file kept at {temp_path}.")
                        return False  # Status stays PAUSED (set by pause_transfer); caller distinguishes via get_transfer
                    chunk_size = min(FILE_BUFFER_LEN, bytes_to_receive - bytes_received)
                    chunk = data_sock.recv(chunk_size)
                    
                    if not chunk:
                        raise OSError("Connection closed by the sender before all data was received.")
                    f.write(chunk)
                    bytes_received += len(chunk)
                    core.update_transfer(transfer_key, received=resume_offset + bytes_received)
                    logger.debug(f"Received {bytes_received}/{bytes_to_receive} bytes")

            actual_hash = get_file_hash(str(temp_path))
            if expected_hash and actual_hash != expected_hash:
                logger.error(f"Hash mismatch for {temp_path}. Expected: {expected_hash}, Actual: {actual_hash}")
                # The reassembled file is proven bad — discard the partial so a
                # later resume can't append to it and instead starts from zero
                # (this is how the "source changed between pause/resume" edge heals).
                temp_path.unlink(missing_ok=True)
                core.set_transfer_status(transfer_key, TransferStatus.FAILED)
                return False

            # Mirror the source structure under the downloads folder, creating
            # any intermediate directories before moving the finished file.
            final_target = downloads_root / final_rel
            final_target.parent.mkdir(parents=True, exist_ok=True)
            final_dest = get_unique_filename(final_target)
            os.replace(temp_path, final_dest)
            core.set_transfer_status(transfer_key, TransferStatus.COMPLETED)
            logger.info(f"File {remote_file_path} downloaded successfully to {final_dest}")
            return True
        finally:
            data_sock.close()
    
    except (OSError,RequestException) as e:
        logger.error(f"Failed to connect to {owner_ip}:{CLIENT_RECV_PORT} - {e}")
        core.set_transfer_status(transfer_key, TransferStatus.FAILED)
        return False
    
    finally:
        control_sock.close()
        data_listen_sock.close()

def handle_incoming_file_request(core: "ClientCore", conn: socket.socket, peer_ip: str, query: bytes) -> None:
    """Handle incoming file request from a peer client.
    Validates the path security, calculates the hash and streams file back to requester"""

    try:
        req: FileRequest = msgpack.unpackb(query, raw=False)
        rel_path = req["filepath"]
        target_port = req["port"]
        request_hash = req.get("request_hash", False)
        resume_offset = req.get("resume_offset",0)

        share_root = SHARE_FOLDER_PATH.resolve()
        requested_file = (share_root / rel_path).resolve()

        if not requested_file.is_relative_to(share_root) or not requested_file.is_file():
            logger.warning(f"Rejected file request for {requested_file} from {peer_ip} for invalid path")
            send_error(conn, RequestException(msg="Invalid file path", code=ExceptionCodes.NOT_FOUND))
            return
        #Lazy hash calculation, only if the requester wants it
        # If the requester set the request_hash option, the server does not have the hash of the file
                # so send the updated hash to the server
        if request_hash:
            file_hash = get_file_hash(str(requested_file))
            core.updated_file_hash_on_server(rel_path,file_hash)

        data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_sock.settimeout(5.0)

        try:
            logger.debug(f"Connecting to {peer_ip}:{target_port} for file transfer")
            data_sock.connect((peer_ip, target_port))
            data_sock.settimeout(None)  # Remove timeout after connection

            file_size = requested_file.stat().st_size
            with open(requested_file, "rb") as f:
                if resume_offset > 0:
                    f.seek(resume_offset)
                while True:
                    chunk = f.read(FILE_BUFFER_LEN)
                    if not chunk:
                        break
                    data_sock.sendall(chunk)
                logger.info(f"Successfully sent file {requested_file} to {peer_ip}:{target_port}")
        finally:
            data_sock.close()

    except Exception as e:
        logger.error(f"Error handling file request from {peer_ip}: {e}")

def download_folder(
        core: "ClientCore",
        owner_name: str,
        folder_dirdata: DirData
                    ) -> bool:

    """Recursively downloads a folder from a remote client via P2P connection.
    Flattens the DirData structure into a list of files and downloads each file individually."""

    files_to_download: list[DirData] = []

    children = folder_dirdata.get("children", []) if folder_dirdata.get("type") == "directory" else [folder_dirdata]
    get_files_in_dir(children, files_to_download)

    if not files_to_download:
        logger.info(f"No files to download in folder {folder_dirdata.get('name')}")
        return True

    logger.info(f"Starting download of folder {folder_dirdata.get('name')} with {len(files_to_download)} files from {owner_name}")

    overall_success = True
    for item in files_to_download:
        remote_file_path = item["path"]
        filesize = item["size"]
        expected_hash = item.get("hash")

        success = download_file(
            core=core,
            owner_name=owner_name,
            remote_file_path=remote_file_path,
            filesize=filesize,
            expected_hash=expected_hash,
            dest_subpath=remote_file_path,  # mirror the share tree under Downloads
        )
        if not success:
            # A False return can mean PAUSED (resume later) or genuinely FAILED.
            # Consult the transfer status to tell them apart: a pause stops the
            # whole folder, but a single failed file doesn't halt the rest.
            rec = core.get_transfer(_transfer_key(owner_name, remote_file_path))
            if rec is not None and rec["status"] == TransferStatus.PAUSED:
                logger.info(f"Folder download paused at {remote_file_path}; remaining files not started.")
                return False
            logger.error(f"Failed to download file {remote_file_path} from {owner_name}")
            overall_success = False

    return overall_success


def resume_download(
        core: "ClientCore",
        owner_name: str,
        file_item: DirData,
        dest_subpath: Optional[str] = None,
) -> bool:
    """Resume a paused/interrupted download by measuring the partial temp file
    and re-issuing the request from that offset. Pass the same file_item and
    dest_subpath the original download used."""

    remote_file_path = file_item["path"]
    filesize = file_item["size"]
    expected_hash = file_item.get("hash")

    temp_path = _temp_path_for(owner_name, remote_file_path)
    resume_offset = temp_path.stat().st_size if temp_path.exists() else 0

    # The temp file IS the resume state: its size == bytes already confirmed.
    # If it's somehow larger than the source (source shrank / corruption), the
    # partial can't be trusted — discard it and start fresh.
    if resume_offset > filesize:
        logger.warning(
            f"Partial for {remote_file_path} ({resume_offset}B) exceeds source size "
            f"({filesize}B); discarding and restarting from zero."
        )
        temp_path.unlink(missing_ok=True)
        resume_offset = 0

    if resume_offset == 0:
        logger.info(f"No usable partial for {remote_file_path}; downloading from the start.")
    else:
        logger.info(f"Resuming {remote_file_path} from offset {resume_offset}/{filesize} bytes.")

    # download_file does the rest: opens the temp in append mode, sends
    # resume_offset in the FILE_REQUEST (sender seeks to it), verifies the hash
    # on the FULL reassembled file, then moves it into Downloads.
    return download_file(
        core=core,
        owner_name=owner_name,
        remote_file_path=remote_file_path,
        filesize=filesize,
        resume_offset=resume_offset,
        expected_hash=expected_hash,
        dest_subpath=dest_subpath,
    )


def send_direct_transfer(core: "ClientCore", target_uname: str, local_path: str) -> bool:
    """Push a file to a specific user — consent handshake (step 4.7.1).

    Resolves the target, sends DIRECT_TRANSFER_REQUEST with the file metadata,
    and waits for the receiver's decision: a bare-frame ack means accepted, an
    'e' reply raises RequestException (UNAUTHORIZED == declined). Returns True
    if accepted. The actual byte stream over the same socket is step 4.7.2."""

    file_path = Path(local_path)
    if not file_path.is_file():
        logger.error(f"Cannot send: '{local_path}' is not a file.")
        return False

    target_ip = core.request_peer_ip(target_uname)
    if not target_ip:
        logger.error(f"Cannot send: could not resolve IP for '{target_uname}' (offline?).")
        return False

    # Metadata the receiver consents to. compression is an enum -> send .value
    # so msgpack can serialize it. The hash lets the receiver verify (4.7.2).
    metadata: FileMetaData = {
        "path": file_path.name,
        "size": file_path.stat().st_size,
        "hash": get_file_hash(str(file_path)),
        "compression": CompressionMethod.NONE.value,
    }

    peer_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    peer_sock.settimeout(5.0)
    try:
        peer_sock.connect((target_ip, CLIENT_RECV_PORT))
        peer_sock.settimeout(None)  # Remove timeout after connection

        send_msgpack(peer_sock, HeaderCode.DIRECT_TRANSFER_REQUEST, metadata)
        logger.info(f"Requested to send '{file_path.name}' to {target_uname}; awaiting consent...")

        reply = receive_message(peer_sock)
        if reply["type"] == HeaderCode.DIRECT_TRANSFER_REQUEST:
            logger.info(f"{target_uname} accepted the transfer of '{file_path.name}'.")
            # --- 4.7.2: stream the file over peer_sock (metadata frame + bytes) ---
            return True

        logger.error(f"Unexpected reply to direct-transfer request: {reply['type']}")
        return False

    except RequestException as e:
        if e.code == ExceptionCodes.UNAUTHORIZED:
            logger.info(f"{target_uname} declined the transfer of '{file_path.name}'.")
        else:
            logger.error(f"Direct-transfer request to {target_uname} failed: {e.msg}")
        return False
    except OSError as e:
        logger.error(f"Failed to reach {target_uname} at {target_ip}: {e}")
        return False
    finally:
        peer_sock.close()


def handle_incoming_direct_transfer_request(core: "ClientCore", conn: socket.socket, peer_ip: str, query: bytes) -> None:
    """Consent handshake for an inbound direct transfer / push (step 4.7.1).

    Unpacks the sender's FileMetaData, asks the consent policy, and either acks
    (accept) or replies UNAUTHORIZED (reject). On accept the same socket stays
    open for the byte stream (step 4.7.2)."""

    try:
        metadata: FileMetaData = msgpack.unpackb(query, raw=False)

        # Validate the shape before trusting it.
        if not isinstance(metadata, dict) or "path" not in metadata or "size" not in metadata:
            logger.warning(f"Malformed direct-transfer request from {peer_ip}.")
            send_error(conn, RequestException(msg="Malformed transfer metadata", code=ExceptionCodes.BAD_REQUEST))
            return

        # The SENDER chose `path`, and it dictates where the file lands on OUR
        # disk — strip any directory components so a '../' can't escape the
        # downloads folder (4.7.2 saves under this name).
        safe_name = Path(metadata["path"]).name
        sender = core.request_peer_uname(peer_ip) or peer_ip

        if not core.should_accept_transfer(metadata):
            logger.info(f"Direct transfer of '{safe_name}' from {sender} rejected.")
            send_error(conn, RequestException(msg="Transfer declined by recipient", code=ExceptionCodes.UNAUTHORIZED))
            return

        # Accept: bare-frame ack (same shape as the registration ack). The
        # sender's receive_message sees this type and knows it may stream.
        send_message(conn, HeaderCode.DIRECT_TRANSFER_REQUEST)
        logger.info(f"Accepted direct transfer of '{safe_name}' ({metadata['size']} bytes) from {sender}.")

        # --- 4.7.2: receive the DIRECT_TRANSFER stream on `conn`, verify the
        #     hash from metadata, write to DIRECT_TEMP_FOLDER_PATH, then move to
        #     the downloads folder under safe_name. Not built yet. ---

    except RequestException as e:
        # e.g. the peer dropped during the handshake — nothing more to reply to.
        logger.warning(f"Direct-transfer request from {peer_ip} failed: {e.msg}")
    except Exception as e:
        logger.error(f"Error handling direct-transfer request from {peer_ip}: {e}", exc_info=True)






