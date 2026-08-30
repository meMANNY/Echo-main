# Echo — LAN P2P File Sharing & Chat

Echo is a peer-to-peer file-sharing and chat application for a local network. A
lightweight central server handles presence and lookup; the actual chat and file
transfers happen **directly between peers**. The engine (`client/core.py`,
`client/peer_listener.py`, `client/server_connection.py`) is UI-free and
headless-testable; a PyQt5 GUI sits on top of it through a single `CoreAdapter`
seam.

## Requirements

- **Python 3.11**
- Dependencies: `PyQt5`, `msgpack`, `tinydb`, `notify-py`, `watchdog`, `fuzzysearch`

## Setup

From the `Echo-main` directory (PowerShell):

```powershell
# Create and populate a virtual environment
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install pyqt5 msgpack tinydb notify-py watchdog fuzzysearch
```

All commands below call the venv Python directly (`.\.venv\Scripts\python.exe`),
so they work whether or not the venv is "activated". To activate it instead, run
`.\.venv\Scripts\Activate.ps1` and then use plain `python`.

## Running

Echo has three processes: **one server** and **one client per user**. Run each in
its own terminal, all from the `Echo-main` directory.

**1. Start the server** (on one machine — note its LAN IP, e.g. from `ipconfig`):

```powershell
.\.venv\Scripts\python.exe -m server.server
```

**2. Start a client** (on each machine / for each user):

```powershell
.\.venv\Scripts\python.exe -m client.app
```

On first launch the client shows a setup window: enter a **unique username**, the
**server's LAN IP**, a **share folder**, and a **downloads folder**. Later launches
skip setup and auto-connect. Windows Firewall will prompt the first time — allow
Python on **private networks** (the server listens on `1234`, each client on
`4321` for inbound peer connections).

Two clients on a **single machine** work for quick testing (use two usernames and
two share/downloads folders); a genuine two-machine LAN is needed for the final
sign-off, since chat sender-attribution is ambiguous when both peers share
`127.0.0.1`.

## Data locations

Everything lives under `~/.Echo` (`%USERPROFILE%\.Echo` on Windows):

| Path | Contents |
|------|----------|
| `~/.Echo/db/settings.json` | user settings (username, server IP, folders) |
| `~/.Echo/db/transfer_journal.json` | in-progress/paused transfers (survives restarts) |
| `~/.Echo/share` | default share folder |
| `~/.Echo/tmp`, `~/.Echo/direct` | partial-download temp files |
| `~/Downloads` | default downloads folder |

## Testing

### Headless engine test (fast, one machine)

`verify_phase4.py` exercises the whole transport engine (register, presence,
browse, search, chat, file + folder download, pause/resume, journal, direct
transfer) — 24 checks. It needs the server running. Two terminals:

```powershell
# Terminal 1 — server
.\.venv\Scripts\python.exe -m server.server

# Terminal 2 — the test
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe verify_phase4.py
```

Pass = `PASS: 24   FAIL: 0`. This is the fastest regression check after any change
to the core.

### GUI acceptance tests (two clients)

Run the server + two GUI clients (ideally two machines) and walk through:

1. **First-run flow** — fresh start → setup → connected; `settings.json` created;
   relaunch auto-connects straight to the main window.
2. **Presence** — each user appears online within ~5 s; killing one greys it with a
   "last active" time within ~10 s; selection enables/disables Send + Refresh.
3. **Browse + search** — selecting a user renders their tree (empty share → a
   placeholder, not an error); File Info shows a readable size; search finds files
   with owner attribution; Go-to-owner navigates; dropping a file into a share
   shows up on the other side's Refresh within a second or two (auto-rescan).
4. **Chat** — messages render both ways with history; over-256-byte input is
   blocked; a minimized window pops a desktop notification.
5. **Transfers** — download a file **and** a folder with live progress; pause →
   resume → hash verifies; kill the app mid-download → relaunch shows the paused
   row from the journal → resume completes; a direct transfer pops the consent
   dialog (accept lands the file, decline is clean).
6. **Responsiveness** — during a large transfer the window stays responsive; stop
   the server mid-session → the banner flips to Disconnected with no dialog storm →
   Reconnect works once the server returns.

### Reset helpers

```powershell
# Re-trigger the first-run flow (wipes settings + share + journal)
Remove-Item -Recurse -Force "$env:USERPROFILE\.Echo"

# Clear only a stuck transfer journal
Remove-Item -Force "$env:USERPROFILE\.Echo\db\transfer_journal.json"
```
