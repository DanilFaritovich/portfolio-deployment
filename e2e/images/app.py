"""No dependencies. Distinct immutable v1/v2/bad/worker/data images."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import sys
import time
import uuid

MODE = os.environ.get("FIXTURE_MODE", "http")
VERSION = os.environ.get("FIXTURE_VERSION", "v1")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            status, body = (503 if MODE == "bad" else 200), VERSION.encode()
        elif self.path == "/data" and MODE == "data":
            status, body = 200, Path("/data/marker").read_bytes()
        else:
            status, body = 404, b"missing"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if sys.argv[1:] == ["healthcheck"]:
        sys.exit(0 if MODE == "worker" and Path("/tmp/fixture-worker").exists() else 1)
    if MODE == "worker":
        Path("/tmp/fixture-worker").write_text("ready")
        while True:
            time.sleep(60)
    else:
        if MODE == "data":
            marker = Path("/data/marker")
            if not marker.exists():
                marker.write_text(uuid.uuid4().hex)
        HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
