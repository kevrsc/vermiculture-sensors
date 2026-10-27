"""Accept temperature posts from the ESP32 and graph the log."""

import json
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8080
LOG_PATH = Path(__file__).with_name("readings.jsonl")

latest = None

PAGE_PATH = Path(__file__).resolve().parent.parent / "web" / "index.html"


def gateway() -> str:
    """Address used only to ask the OS which local interface would reach it."""
    try:
        from config import GATEWAY
    except ImportError:
        return "8.8.8.8"
    return str(GATEWAY)


def lan_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((gateway(), 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def read_log() -> list:
    if not LOG_PATH.exists():
        return []
    rows = []
    with LOG_PATH.open(encoding="utf-8") as log:
        for line in log:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/readings":
            payload = json.dumps(latest).encode() if latest is not None else b"null"
            self._send(200, "application/json; charset=utf-8", payload)
            return
        if path == "/history":
            payload = json.dumps(read_log(), separators=(",", ":")).encode()
            self._send(200, "application/json; charset=utf-8", payload)
            return
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE_PATH.read_bytes())
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/readings":
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            reading = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, "text/plain; charset=utf-8", b"expected JSON")
            return
        if not isinstance(reading, dict):
            self._send(400, "text/plain; charset=utf-8", b"expected a JSON object")
            return

        if not isinstance(reading.get("received_at"), str) or not reading["received_at"].strip():
            reading["received_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        global latest
        latest = reading
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(json.dumps(reading) + "\n")
        print(json.dumps(reading), flush=True)
        self._send(204, "text/plain; charset=utf-8", b"")

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
    global latest
    rows = read_log()
    latest = rows[-1] if rows else None
    ip = lan_ip()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"On this PC:  http://localhost:{PORT}")
    print(f"ESP32 posts: http://{ip}:{PORT}/readings")
    print("Leave this window open.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
