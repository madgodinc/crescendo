"""Crescendo dashboard server — a read-only vitrine on top of the live run.

Serves the static dashboard files AND proxies three read-only JSON endpoints to
mgi-mind's KV store, adding the bearer token server-side. The dashboard is a
browser page; mgi-mind has no CORS and the token must not reach the browser, so
this same-origin proxy is the seam. Everything is loopback.

  GET /                     -> dashboard/index.html
  GET /<file>               -> static file from the dashboard dir
  GET /api/runs             -> {current, recent:[...]}        (CRESCENDO_ACTIVE)
  GET /api/live             -> the current run's live document (or {status:idle})
  GET /api/run/<run_id>     -> a specific run's live document  (or 404)

Run: uv run python dashboard/serve.py [--port 8000]
mgi-mind must be up on MGIMIND_URL; MGIMIND_TOKEN_ARCHIVIST supplies the token.
"""

import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv("/home/madgodinc/code/crescendo/.env")

MGIMIND_URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765").rstrip("/")
TOKEN = os.environ["MGIMIND_TOKEN_ARCHIVIST"]
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

LIVE_MARKER = "CRESCENDO_LIVE"
ACTIVE_KEY = "CRESCENDO_ACTIVE"

# Only these static files are servable (no directory traversal, no surprises).
_MIME = {".html": "text/html", ".js": "application/javascript",
         ".css": "text/css", ".json": "application/json", ".ico": "image/x-icon"}


def _kv_get(key: str):
    """Fetch a KV value from mgi-mind, or None. Token added here, server-side."""
    body = json.dumps({"key": key}).encode("utf-8")
    req = urllib.request.Request(
        f"{MGIMIND_URL}/kv/get", data=body, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("value") if data.get("found") else None


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, rel: str):
        # Resolve inside the dashboard dir only — reject traversal.
        path = os.path.normpath(os.path.join(DASHBOARD_DIR, rel))
        if not path.startswith(DASHBOARD_DIR) or not os.path.isfile(path):
            self.send_error(404, "not found")
            return
        ext = os.path.splitext(path)[1]
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/runs":
                active = _kv_get(ACTIVE_KEY) or {"current": None, "recent": []}
                return self._send_json(active)
            if path == "/api/live":
                active = _kv_get(ACTIVE_KEY) or {}
                cur = active.get("current")
                doc = _kv_get(f"{LIVE_MARKER}:{cur}") if cur else None
                return self._send_json(doc or {"status": "idle"})
            if path.startswith("/api/run/"):
                run_id = path[len("/api/run/"):]
                doc = _kv_get(f"{LIVE_MARKER}:{run_id}")
                return self._send_json(doc) if doc else self._send_json(
                    {"error": "run not found"}, status=404)
        except Exception as e:
            # mgi-mind unreachable / bad reply — tell the front-end, don't hang.
            return self._send_json({"error": f"memory unreachable: {e}"}, status=502)

        # static
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        self._send_file(rel)

    def log_message(self, *a):  # quiet; the run logs are what matter
        pass


def main():
    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[dashboard] http://127.0.0.1:{port}/  (proxying {MGIMIND_URL})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
