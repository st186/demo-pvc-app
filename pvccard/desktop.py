"""Desktop launcher: start the server and open PVC Studio in its own window.

    pythonw -m pvccard.desktop          (what the Windows shortcut runs)
    python  -m pvccard.desktop --no-browser

Opens a Chrome or Edge "app" window (no tabs or address bar) with its own
browser profile, and stops the server when that window is closed. If PVC
Studio is already running, it just opens another window onto it. Logs go to
%LOCALAPPDATA%\\PVC Studio\\logs\\server.log because pythonw has no console.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "PVC Studio"
PORTS = range(8000, 8020)


def install_root() -> Path | None:
    """Root of a packaged install (python\\ and models\\ live there), else None."""
    root = Path(sys.executable).resolve().parent.parent
    return root if (root / "pvc-studio.portable").exists() else None


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_pvc_studio(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/passport/info", timeout=1) as r:
            return "default_quality" in json.load(r)
    except Exception:
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int | None = None) -> tuple[int, bool]:
    """(port, already_running). Reuses a running PVC Studio, else a free port."""
    ports = [preferred] if preferred else list(PORTS)
    for p in ports:
        if _is_pvc_studio(p):
            return p, True
    for p in ports:
        if _port_free(p):
            return p, False
    raise SystemExit(f"No free port in {ports[0]}-{ports[-1]}")


def find_browser() -> str | None:
    """Chrome, then Edge (always present on Windows 10/11), then Chromium."""
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        rel = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"]
        for r in rel:
            for root in filter(None, roots):
                p = Path(root) / r
                if p.exists():
                    return str(p)
        return None
    if sys.platform == "darwin":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
            if Path(p).exists():
                return p
        return None
    return next(filter(None, map(shutil.which, (
        "google-chrome", "google-chrome-stable", "microsoft-edge", "chromium", "chromium-browser"))), None)


def open_window(url: str) -> subprocess.Popen | None:
    """App window with a dedicated profile, so its process lives as long as the
    window. Falls back to the default browser (returns None)."""
    exe = find_browser()
    if not exe:
        webbrowser.open(url)
        return None
    profile = data_dir() / "browser-profile"
    return subprocess.Popen([
        exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
        "--no-default-browser-check", "--disable-features=Translate", "--window-size=1440,900",
    ])


def _log_to_file() -> None:
    logs = data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    f = open(logs / "server.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = f
    print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting {APP_NAME} ---")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pvccard.desktop")
    ap.add_argument("--port", type=int, help="default: first free port from 8000")
    ap.add_argument("--no-browser", action="store_true", help="only run the server")
    args = ap.parse_args(argv)

    root = install_root()
    if root:
        # Models shipped with (and later downloaded into) the install folder.
        os.environ.setdefault("U2NET_HOME", str(root / "models"))
    if sys.stdout is None or root:  # pythonw has no console
        _log_to_file()

    port, running = pick_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    if running:
        if not args.no_browser:
            open_window(url)
        return

    import uvicorn
    server = uvicorn.Server(uvicorn.Config("pvccard.web:app", host="127.0.0.1", port=port,
                                           log_level="info", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 120
    while not _is_pvc_studio(port):
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit(f"{APP_NAME} server did not start; see the log")
        time.sleep(0.2)
    print(f"{APP_NAME} running at {url}")

    window = None if args.no_browser else open_window(url)
    if window is None:
        thread.join()  # no app window to watch: run until stopped
        return
    window.wait()
    # The window is closed (or Chrome handed it to a window that was already
    # open on this profile); keep serving while any window still uses us.
    while _profile_in_use():
        time.sleep(2)
    server.should_exit = True
    thread.join(timeout=10)


def _profile_in_use() -> bool:
    """True while a browser still holds our profile (another PVC Studio window)."""
    lock = data_dir() / "browser-profile" / ("lockfile" if sys.platform == "win32" else "SingletonLock")
    if sys.platform == "win32":
        # Chrome keeps the lockfile open while running; deleting it fails then.
        try:
            lock.unlink()
            return False
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return lock.is_symlink() or lock.exists()


if __name__ == "__main__":
    main()
