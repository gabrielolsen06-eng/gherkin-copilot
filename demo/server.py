"""Serve the demo shop on localhost only.

    python demo/server.py            # open http://127.0.0.1:8000 in a browser

The tests start their own copy on a random free port, so you never need to
run this by hand for the test suite.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent / "app"
HOST = "127.0.0.1"  # never listen on the network, only on this machine

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class DemoHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def list_directory(self, path: str | os.PathLike[str]) -> None:  # no directory listings
        self.send_error(404)
        return None

    def do_POST(self) -> None:  # static site: GET/HEAD only
        self.send_error(405)

    def log_message(self, format: str, *args: Any) -> None:  # keep test output clean
        pass


def make_server(port: int = 0) -> ThreadingHTTPServer:
    handler = partial(DemoHandler, directory=str(APP_DIR))
    return ThreadingHTTPServer((HOST, port), handler)


def start_in_background(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = make_server(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{HOST}:{server.server_address[1]}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args().port
    with make_server(port) as server:
        print(f"Demo shop running at http://{HOST}:{port}  (Ctrl+C to stop)")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
