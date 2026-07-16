"""Start VCE Read Aloud and open it in the default browser.

Usage:  python run.py            (or double-click start.bat on Windows)
        python run.py --no-browser --port 8471
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn

DEFAULT_PORT = 8471


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_browser_when_ready(port: int) -> None:
    for _ in range(100):
        if port_in_use(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description="VCE Read Aloud")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if port_in_use(args.port):
        # Already running (e.g. double-clicked twice) — just open the page.
        print(f"VCE Read Aloud is already running on port {args.port}; opening browser.")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(args.port,), daemon=True).start()

    print(f"Starting VCE Read Aloud at http://127.0.0.1:{args.port}  (Ctrl+C to quit)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
