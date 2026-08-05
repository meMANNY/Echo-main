"""
Phase 4 verification harness (THROWAWAY — delete once Phase 4 is signed off).

Sibling of verify_phase3.py, but for the real ClientCore. Runs the six tests
from "HOW TO VERIFY PHASE 4 IS DONE" (PHASE4_CLIENT_PLAN.txt) in ONE process
against a running Phase 3 server.

Single-machine caveats (documented in the plan):
  - Both cores share one host IP, so REQUEST_UNAME-by-IP is ambiguous — peer
    NAME attribution in chat / direct-transfer is unreliable on localhost, but
    the bytes/mechanisms are exercised for real. We assert on delivery, not on
    the derived sender name.
  - Two peer listeners can't share CLIENT_RECV_PORT (4321) on one box, so the
    P2P tests run ONE listener (bob's) and use alice as the initiator.

USAGE (server first, in the venv):
    .venv/Scripts/python.exe -m server.server        # terminal 1
    .venv/Scripts/python.exe verify_phase4.py         # terminal 2
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from utils.socket_functions import get_self_ip
from utils.constants import SHARE_FOLDER_PATH, ONLINE_TIMEOUT
from utils.types import TransferStatus
from client.core import ClientCore
from client.peer_listener import PeerListener
from client import transfers

# files we drop into the (shared) share root; cleaned up at the end
_ARTIFACTS: list[Path] = []

SERVER_IP = get_self_ip()

_passes: list[str] = []
_fails: list[str] = []


def check(cond: bool, msg: str) -> bool:
    (_passes if cond else _fails).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return cond


def test_1_and_2() -> tuple[ClientCore, ClientCore]:
    # A known file under the (shared) share root so browse/search have a target.
    SHARE_FOLDER_PATH.mkdir(parents=True, exist_ok=True)
    sample = SHARE_FOLDER_PATH / "echo_verify_sample.txt"
    sample.write_text("hello echo phase 4 verification\n" * 200, encoding="utf-8")

    alice = ClientCore()
    bob = ClientCore()
    for c, name in ((alice, "alice"), (bob, "bob")):
        c.settings["uname"] = name
        c.settings["share_folder_path"] = str(SHARE_FOLDER_PATH)

    print("== TEST 1: foundation (register / publish / presence) ==")
    check(alice.connect_to_server(SERVER_IP), "alice connected to server")
    check(bob.connect_to_server(SERVER_IP), "bob connected to server")
    check(alice.register("alice"), "alice registered")
    check(bob.register("bob"), "bob registered")
    check(alice.publish_share_data(), "alice published share data")
    check(bob.publish_share_data(), "bob published share data")

    print("  ...waiting ~7s for heartbeat to see peers online...")
    time.sleep(7)
    check("bob" in alice.online_peers, f"alice sees bob ONLINE (online={list(alice.online_peers)})")
    check("alice" in bob.online_peers, f"bob sees alice ONLINE (online={list(bob.online_peers)})")

    # Simulate bob dropping: close its server link + stop it heartbeating.
    bob.server.close()
    bob._registered = False
    wait = ONLINE_TIMEOUT + 6
    print(f"  ...bob dropped; waiting {wait}s for alice to see it go OFFLINE...")
    time.sleep(wait)
    check("bob" not in alice.online_peers, f"alice sees bob OFFLINE (online={list(alice.online_peers)})")

    # Bring bob back for the P2P tests.
    check(bob.connect_to_server(SERVER_IP), "bob reconnected")
    check(bob.register("bob"), "bob re-registered")
    bob.publish_share_data()

    print("== TEST 2: browse + search (server-mediated) ==")
    tree = alice.browse("bob")
    check(tree is not None and len(tree) > 0,
          f"alice browsed bob's tree ({'None' if tree is None else len(tree)} top-level items)")
    results = alice.search("echo_verify_sample")
    found = results is not None and any(r.get("owner") == "bob" for r in results)
    check(found, f"search found bob's file with owner attribution ({'None' if results is None else len(results)} match(es))")

    return alice, bob


def _find(tree, name):
    """Find a top-level DirData node by name in a browse() result."""
    for node in (tree or []):
        if node.get("name") == name:
            return node
    return None


def test_3_to_6(alice: ClientCore, bob: ClientCore) -> None:
    # bob is the only listener (single-machine 4321 limit); alice initiates.
    bob_listener = PeerListener(bob)
    bob_listener.start()
    time.sleep(0.5)

    # isolated download destinations so we can verify what landed where
    alice_dl = Path(tempfile.mkdtemp(prefix="echo_alice_dl_"))
    bob_dl = Path(tempfile.mkdtemp(prefix="echo_bob_dl_"))
    alice.settings["downloads_folder_path"] = str(alice_dl)
    bob.settings["downloads_folder_path"] = str(bob_dl)

    sample = SHARE_FOLDER_PATH / "echo_verify_sample.txt"

    print("== TEST 3: P2P chat (alice -> bob) ==")
    sent = alice.send_chat_message("bob", "hello from alice")
    time.sleep(1.0)  # bob handles on a listener worker thread
    delivered = any(m["content"] == "hello from alice"
                    for msgs in bob.message_history.values() for m in msgs)
    check(sent and delivered, "chat message delivered into bob's history (sender label ambiguous on localhost)")

    print("== TEST 4: download single + folder + lazy hash ==")
    size = sample.stat().st_size
    ok = transfers.download_file(alice, "bob", "echo_verify_sample.txt", size)
    dest = alice_dl / "echo_verify_sample.txt"
    check(ok and dest.exists() and dest.read_bytes() == sample.read_bytes(),
          "single-file download completed + SHA-1 verified + bytes match source")
    # 4.5.4 lazy hash: first download should have filled the server's record
    node = _find(alice.browse("bob"), "echo_verify_sample.txt")
    check(bool(node and node.get("hash")), "lazy hash propagated to server (browse now shows a hash)")

    # folder download
    folder = SHARE_FOLDER_PATH / "verify_folder"
    folder.mkdir(exist_ok=True)
    (folder / "a.txt").write_text("a" * 5000, encoding="utf-8")
    (folder / "b.txt").write_text("b" * 9000, encoding="utf-8")
    _ARTIFACTS.append(folder)
    bob.publish_share_data()
    fnode = _find(alice.browse("bob"), "verify_folder")
    ok = fnode is not None and transfers.download_folder(alice, "bob", fnode)
    got_a = (alice_dl / "verify_folder" / "a.txt")
    got_b = (alice_dl / "verify_folder" / "b.txt")
    check(bool(ok) and got_a.exists() and got_b.exists()
          and got_a.read_text() == "a" * 5000 and got_b.read_text() == "b" * 9000,
          "folder download mirrored the tree + both files verified")

    print("== TEST 5: pause / resume / journal ==")
    big = SHARE_FOLDER_PATH / "verify_big.bin"
    big.write_bytes((b"ECHO_PHASE4_" * 90)[:1024 * 1024] * 80)  # ~80 MB
    _ARTIFACTS.append(big)
    bigsize = big.stat().st_size
    key = transfers._transfer_key("bob", "verify_big.bin")
    result: dict = {}

    def _dl():
        result["ok"] = transfers.download_file(alice, "bob", "verify_big.bin", bigsize)

    t = threading.Thread(target=_dl, daemon=True)
    t.start()
    paused = False
    for _ in range(40000):
        rec = alice.get_transfer(key)
        if rec and 0 < rec["progress"] < bigsize:
            alice.pause_transfer(key)
            paused = True
            break
        time.sleep(0.0005)
    t.join(timeout=30)
    check(paused, "caught the download mid-stream and paused it")
    rec = alice.get_transfer(key)
    check(bool(rec) and rec["status"] == TransferStatus.PAUSED, "transfer status is PAUSED")
    temp = transfers._temp_path_for("bob", "verify_big.bin")
    check(temp.exists() and temp.stat().st_size < bigsize, "partial temp kept + smaller than full size")
    resumable = alice.get_resumable_transfers()
    check(any(e["filepath"] == "verify_big.bin" for e in resumable),
          "journal lists the paused transfer as resumable (survives a restart)")
    # resume the rest
    ok = transfers.resume_download(alice, "bob", {"path": "verify_big.bin", "size": bigsize, "hash": None})
    bigdest = alice_dl / "verify_big.bin"
    check(bool(ok) and bigdest.exists() and bigdest.stat().st_size == bigsize
          and bigdest.read_bytes() == big.read_bytes(),
          "resume completed the file + full-size + bytes match source")

    print("== TEST 6: direct transfer (push) accept + reject ==")
    bob.auto_accept_transfers = True
    ok = transfers.send_direct_transfer(alice, "bob", str(sample))
    time.sleep(0.5)
    pushed = bob_dl / "echo_verify_sample.txt"
    check(bool(ok) and pushed.exists() and pushed.read_bytes() == sample.read_bytes(),
          "direct transfer accepted + delivered to bob + verified")
    bob.auto_accept_transfers = False
    ok = transfers.send_direct_transfer(alice, "bob", str(sample))
    check(ok is False, "direct transfer cleanly rejected when receiver declines")

    bob_listener.stop()


def summary() -> int:
    print("\n================ SUMMARY ================")
    print(f"  PASS: {len(_passes)}   FAIL: {len(_fails)}")
    for f in _fails:
        print(f"   - FAIL: {f}")
    return 1 if _fails else 0


def _cleanup() -> None:
    import shutil
    for p in _ARTIFACTS:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    print(f"Server target: {SERVER_IP}\n")
    _ARTIFACTS.append(SHARE_FOLDER_PATH / "echo_verify_sample.txt")
    try:
        alice, bob = test_1_and_2()
        test_3_to_6(alice, bob)
    finally:
        _cleanup()
    sys.exit(summary())


if __name__ == "__main__":
    main()
