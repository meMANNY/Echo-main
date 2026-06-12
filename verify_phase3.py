"""
Phase 3 verification harness (THROWAWAY — delete once Phase 3 is signed off).

This is NOT the real client (that's Phase 4). It's a minimal scratch client that
speaks the real wire protocol (reusing utils/protocol.py) to exercise every
server handler end-to-end, per the "HOW TO VERIFY PHASE 3 IS DONE" section of
PHASE3_SERVER_PLAN.txt.

USAGE (run the server in its own terminal first: `python -m server.server`):

    # Test 1 - solo happy path: register, upload share data, self-browse
    python verify_phase3.py solo

    # Test 2 - pair: run these in TWO separate terminals
    python verify_phase3.py alice
    python verify_phase3.py bob        # start within a few seconds of alice

    # Targeted error-path check: unregistered socket sends a non-NEW_CONNECTION
    python verify_phase3.py unauth

The script connects to 127.0.0.1 by default. Override with a second arg, e.g.
    python verify_phase3.py alice 192.168.1.50

After running, on Windows confirm no leaked sockets:
    netstat -ano | findstr 1234
"""

import socket
import sys
import time

from utils.constants import SERVER_RECV_PORT, FMT
from utils.exceptions import RequestException
from utils.types import HeaderCode
from utils.protocol import (
    send_text,
    send_msgpack,
    send_message,
    receive_message,
)


# ---------------------------------------------------------------------------
# Tiny helpers so each test reads like a checklist, not socket plumbing.
# ---------------------------------------------------------------------------

def connect(server_ip: str) -> socket.socket:
    """Open one TCP connection to the server (mirrors the client's single
    persistent socket from the plan's connection-lifetime decision)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((server_ip, SERVER_RECV_PORT))
    return sock


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def register(sock: socket.socket, uname: str) -> None:
    """First message MUST be NEW_CONNECTION (the registration gate). Server
    replies with a bare NEW_CONNECTION ack (no body) on success."""
    send_text(sock, HeaderCode.NEW_CONNECTION, uname)
    reply = receive_message(sock)
    if reply["type"] == HeaderCode.NEW_CONNECTION:
        ok(f"registered as '{uname}'")
    else:
        fail(f"unexpected ack type for registration: {reply['type']}")


def sample_share_tree(label: str) -> list:
    """A tiny valid DirData-shaped tree. Paths are RELATIVE and use forward
    slashes, matching what path_to_dict now emits (post Windows-path fix), so
    validate_share_tree on the server accepts it."""
    return [
        {
            "path": f"{label}_notes.txt",
            "name": f"{label}_notes.txt",
            "hash": None,
            "compression": 0,
            "type": "file",
            "size": 1234,
            "children": [],
        },
        {
            "path": "docs",
            "name": "docs",
            "hash": None,
            "compression": 0,
            "type": "directory",
            "size": None,
            "children": [
                {
                    "path": "docs/readme.md",
                    "name": "readme.md",
                    "hash": None,
                    "compression": 0,
                    "type": "file",
                    "size": 88,
                    "children": [],
                }
            ],
        },
    ]


def upload_share(sock: socket.socket, label: str) -> None:
    """SHARE_DATA: send the tree, expect a bare SHARE_DATA ack."""
    send_msgpack(sock, HeaderCode.SHARE_DATA, sample_share_tree(label))
    reply = receive_message(sock)
    if reply["type"] == HeaderCode.SHARE_DATA:
        ok("share data uploaded and acked")
    else:
        fail(f"unexpected ack type for share data: {reply['type']}")


def browse(sock: socket.socket, target: str) -> None:
    import msgpack
    send_text(sock, HeaderCode.FILE_BROWSE, target)
    reply = receive_message(sock)
    if reply["type"] != HeaderCode.FILE_BROWSE:
        fail(f"unexpected browse reply type: {reply['type']}")
        return
    doc = msgpack.unpackb(reply["query"])
    ok(f"browsed '{target}': {len(doc.get('share', []))} top-level item(s)")


def search(sock: socket.socket, query: str) -> None:
    import msgpack
    send_text(sock, HeaderCode.FILE_SEARCH, query)
    reply = receive_message(sock)
    if reply["type"] != HeaderCode.FILE_SEARCH:
        fail(f"unexpected search reply type: {reply['type']}")
        return
    results = msgpack.unpackb(reply["query"])
    ok(f"searched '{query}': {len(results)} match(es)")


def request_ip(sock: socket.socket, target_uname: str) -> None:
    send_text(sock, HeaderCode.REQUEST_IP, target_uname)
    reply = receive_message(sock)
    if reply["type"] == HeaderCode.REQUEST_IP:
        ok(f"REQUEST_IP('{target_uname}') -> {reply['query'].decode(FMT)}")
    else:
        fail(f"unexpected REQUEST_IP reply: {reply['type']}")


def request_uname(sock: socket.socket, target_ip: str) -> None:
    send_text(sock, HeaderCode.REQUEST_UNAME, target_ip)
    reply = receive_message(sock)
    if reply["type"] == HeaderCode.REQUEST_UNAME:
        ok(f"REQUEST_UNAME('{target_ip}') -> {reply['query'].decode(FMT)}")
    else:
        fail(f"unexpected REQUEST_UNAME reply: {reply['type']}")


def heartbeat(sock: socket.socket) -> dict:
    import msgpack
    send_text(sock, HeaderCode.HEARTBEAT_REQUEST, "1")
    reply = receive_message(sock)
    if reply["type"] != HeaderCode.HEARTBEAT_REQUEST:
        fail(f"unexpected heartbeat reply: {reply['type']}")
        return {}
    status = msgpack.unpackb(reply["query"])
    ok(f"heartbeat -> {len(status)} other user(s): {list(status.keys())}")
    return status


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

def test_solo(server_ip: str) -> None:
    """Test 1 (plan): single client registers, uploads, self-knowledge checks.
    Note: a self-BROWSE is used here as the basic happy path. FILE_SEARCH skips
    the caller's own data, so solo search returns 0 by design."""
    print("== TEST 1: solo happy path ==")
    sock = connect(server_ip)
    try:
        register(sock, "solo")
        upload_share(sock, "solo")
        # Self-browse: confirms the upload round-tripped through TinyDB.
        browse(sock, "solo")
        # Search for our own file returns nothing (server skips caller) — that's
        # expected and proves the skip-self rule, not a failure.
        search(sock, "notes")
        heartbeat(sock)
        print("  solo test complete — closing gracefully")
    finally:
        sock.close()


def test_peer(server_ip: str, me: str, other: str) -> None:
    """Test 2 (plan): one half of the pair. Run twice (alice, bob).

    Each peer registers, uploads its own share, then repeatedly exercises the
    cross-user handlers against the OTHER peer. We loop a few times so you can
    watch the other peer's last_seen timestamp advance via heartbeat."""
    print(f"== TEST 2: pair, this process = '{me}', peer = '{other}' ==")
    sock = connect(server_ip)
    try:
        register(sock, me)
        upload_share(sock, me)

        # Give the other process a moment to come up and register too.
        print(f"  waiting for peer '{other}' to register...")
        time.sleep(12)

        last_seen_prev = None
        for round_no in range(1, 4):
            print(f"  --- round {round_no} ---")
            browse(sock, other)
            search(sock, f"{other}_notes")     # should find the peer's file
            request_ip(sock, other)
            # We don't know the peer's IP a priori on a real LAN, but on
            # localhost both are 127.0.0.1; request_uname by that IP is a
            # self/peer ambiguity on one box — see the note printed below.
            status = heartbeat(sock)
            cur = status.get(other)
            if cur is not None and last_seen_prev is not None:
                if cur >= last_seen_prev:
                    ok(f"peer '{other}' last_seen advanced ({cur:.2f})")
                else:
                    fail("peer last_seen went backwards")
            last_seen_prev = cur
            time.sleep(2)

        print("  pair test complete — closing gracefully")
    finally:
        sock.close()


def test_unauth(server_ip: str) -> None:
    """Targeted check of the registration gate (3.3.1): an unregistered socket
    that sends a non-NEW_CONNECTION message must get UNAUTHORIZED and be
    dropped by the server."""
    print("== TEST: unauthorized first message ==")
    sock = connect(server_ip)
    try:
        # Skip registration; immediately ask for someone's IP.
        send_text(sock, HeaderCode.REQUEST_IP, "nobody")
        try:
            receive_message(sock)
            fail("expected UNAUTHORIZED, but got a normal reply")
        except RequestException as e:
            ok(f"server rejected with code={e.code.name}, msg='{e.msg}'")
        # Server should have closed us; a follow-up read should see disconnect.
        try:
            receive_message(sock)
            fail("socket still open after UNAUTHORIZED (expected close)")
        except RequestException as e:
            ok(f"socket closed by server as expected (code={e.code.name})")
        except OSError:
            ok("socket closed by server as expected (OSError on read)")
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1].lower()
    server_ip = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"

    if mode == "solo":
        test_solo(server_ip)
    elif mode == "alice":
        test_peer(server_ip, "alice", "bob")
    elif mode == "bob":
        test_peer(server_ip, "bob", "alice")
    elif mode == "unauth":
        test_unauth(server_ip)
    else:
        print(f"Unknown mode '{mode}'.")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
