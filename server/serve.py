"""Accept temperature and soil-moisture posts from the ESP32 and show the latest reading."""

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


def lan_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def fahrenheit(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def page(reading) -> str:
    if not reading:
        body = "<p class='wait'>Waiting for the ESP32. It posts about every 15 seconds.</p>"
    else:
        rows = []
        for sensor in reading.get("sensors", []):
            celsius = sensor.get("celsius")
            if celsius is None:
                value = "no reading"
            else:
                value = f"{celsius:.2f} °C  ({fahrenheit(celsius):.2f} °F)"
            address = sensor.get("address", "")
            rows.append(
                f"<tr><td>{sensor.get('index', '')}</td>"
                f"<td><code>{address}</code></td><td>{value}</td></tr>"
            )
        moisture_rows = []
        for sensor in reading.get("moisture", []):
            raw = sensor.get("raw")
            percent = sensor.get("percent")
            if percent is None:
                shown = "no reading"
            else:
                shown = f"{percent}%"
            raw_text = "no reading" if raw is None else str(raw)
            moisture_rows.append(
                f"<tr><td>{sensor.get('index', '')}</td>"
                f"<td>GPIO {sensor.get('pin', '')}</td>"
                f"<td>{shown}</td><td>{raw_text}</td></tr>"
            )
        if moisture_rows:
            moisture = (
                "<h2>Soil moisture</h2>"
                "<table><thead><tr><th>#</th><th>Input</th><th>Moisture</th><th>Raw</th></tr></thead>"
                f"<tbody>{''.join(moisture_rows)}</tbody></table>"
                "<p class='wait'>Percent uses the dry and wet raw limits in the sketch. "
                "Raw is the unscaled ADC value.</p>"
            )
        else:
            moisture = "<p class='wait'>No soil-moisture reading in this post.</p>"
        received = reading.get("received_at", "")
        body = (
            f"<p>Last post {received}</p>"
            "<h2>Temperature</h2>"
            "<table><thead><tr><th>#</th><th>Probe</th><th>Temperature</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            f"{moisture}"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="5">
  <title>Vermiculture sensors</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2.5rem auto; max-width: 40rem; color: #1c1915; }}
    h1 {{ font-weight: normal; font-size: 1.6rem; }}
    h2 {{ font-weight: normal; font-size: 1.15rem; margin-top: 1.6rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #ddd6cc; }}
    code {{ font-family: Consolas, monospace; }}
    .wait {{ color: #6b6258; }}
  </style>
</head>
<body>
  <h1>Vermiculture sensors</h1>
  {body}
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/readings":
            payload = json.dumps(latest).encode() if latest is not None else b"null"
            self._send(200, "application/json; charset=utf-8", payload)
            return
        if path == "/":
            self._send(200, "text/html; charset=utf-8", page(latest).encode())
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
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
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
