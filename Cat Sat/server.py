"""
server.py — the local HTTP server behind the Cat SAT web UI.

Standard library only: no Flask, no FastAPI, nothing new to install. It binds to
127.0.0.1 on a free port, so it is not reachable from anywhere but this machine.

Why a browser at all: the lag was never the database or the Python. It was
CustomTkinter drawing several hundred hand-rendered widgets per screen on the
one thread that also had to stay responsive. A browser does that work on a
compositor thread, caches images itself, and scrolls a thousand rows without
blinking — which is exactly the shape of this app.

    python server.py           # start, print the URL
    python run.py              # start and open it like an app window
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from config import BASE_DIR
from database import init_db

WEB_DIR = BASE_DIR / "web"
HOST = "127.0.0.1"

# Images never change once sat_importer.py has written them, so let the browser
# keep them. This is what makes flipping between questions instant.
IMAGE_CACHE_SECONDS = 60 * 60 * 24 * 30


class CatSatHandler(BaseHTTPRequestHandler):
    server_version = "CatSAT"
    protocol_version = "HTTP/1.1"          # keep-alive: one connection, many requests

    # Without this, every small JSON response waits on the TCP delayed-ACK
    # timer — a flat ~40 ms tax on every single API call, no matter how fast
    # the query was. It was the largest remaining source of lag.
    disable_nagle_algorithm = True

    # ------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):
        if os.environ.get("CATSAT_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status, body=b"", content_type="application/octet-stream",
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, default=str),
                   "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    # --------------------------------------------------------------- routing

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        api = self.server.api

        try:
            if path.startswith("/img/"):
                return self._serve_image(path[len("/img/"):], query.get("kind", "question"))
            if path.startswith("/api/"):
                return self._serve_api(method, path[len("/api/"):], query, api)
            return self._serve_static(path)
        except BrokenPipeError:
            pass                                  # browser navigated away mid-response
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---------------------------------------------------------------- static

    def _serve_static(self, path):
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / relative).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())     # no path traversal
        except ValueError:
            return self._send(403, b"forbidden", "text/plain")
        if not target.is_file():
            # Unknown path: hand back the shell so the client router can deal.
            target = WEB_DIR / "index.html"
            if not target.is_file():
                return self._send(404, b"web/ is missing", "text/plain")

        content_type, _ = mimetypes.guess_type(str(target))
        cache = "no-cache" if target.suffix in (".html",) else "max-age=3600"
        self._send(200, target.read_bytes(),
                   content_type or "application/octet-stream",
                   {"Cache-Control": cache})

    # ---------------------------------------------------------------- images

    def _serve_image(self, question_id, kind):
        question_id = question_id.split("?")[0].strip()
        path = self.server.api.image_path(question_id, kind)
        if not path or not os.path.isfile(path):
            return self._send(404, b"no image", "text/plain",
                              {"Cache-Control": "no-store"})

        stat = os.stat(path)
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        with open(path, "rb") as handle:
            data = handle.read()
        content_type, _ = mimetypes.guess_type(path)
        self._send(200, data, content_type or "image/png", {
            "Cache-Control": f"public, max-age={IMAGE_CACHE_SECONDS}, immutable",
            "ETag": etag,
        })

    # ------------------------------------------------------------------- api

    def _serve_api(self, method, route, query, api):
        body = self._body() if method in ("POST", "DELETE") else {}

        # --- reads
        if method == "GET":
            if route == "bootstrap":
                return self._json(api.bootstrap())
            if route == "plan":
                return self._json(api.plan(query.get("day")))
            if route == "calendar":
                return self._json(api.calendar(query.get("month")))
            if route == "bank":
                return self._json(api.bank(query.get("section")))
            if route == "history":
                return self._json(api.history())
            if route == "dashboard":
                return self._json(api.dashboard())
            if route == "log":
                return self._json(api.log(query.get("session")))
            if route.startswith("review/"):
                return self._json(api.review(route.split("/", 1)[1]))
            if route.startswith("note/"):
                return self._json(api.get_note(route.split("/", 1)[1]))

        # --- writes
        if method == "POST":
            if route == "start/test":
                return self._json(api.start_test(
                    mode=body.get("mode", "section"),
                    sections=body.get("sections") or [],
                    timed=bool(body.get("timed", True)),
                    threshold=float(body.get("threshold", 0.65)),
                    weighted=bool(body.get("weighted", True))))
            if route == "start/drill":
                return self._json(api.start_drill(
                    section=body.get("section"),
                    domains=body.get("domains") or [],
                    count=body.get("count", 8),
                    difficulty=body.get("difficulty") or None,
                    ramp=bool(body.get("ramp", True)),
                    timer=body.get("timer", "Per-question stopwatch"),
                    source=body.get("source", "fresh")))
            if route == "start/redo":
                return self._json(api.start_redo(int(body.get("limit", 10))))
            if route == "start/pool":
                return self._json(api.start_review_pool(
                    body.get("questionIds") or [],
                    body.get("label", "Review session")))
            if route == "submit":
                return self._json(api.submit(body))
            if route == "resume":
                return self._json(api.resume())
            if route == "abandon":
                return self._json(api.abandon())
            if route == "plan/task":
                return self._json(api.set_plan_task(
                    body.get("day"), body.get("key"), body.get("done")))
            if route == "plan/target":
                return self._json(api.add_target(body.get("label")))
            if route == "plan/target/retire":
                return self._json(api.retire_target(body.get("targetId"),
                                                    body.get("accuracy", 0)))
            if route == "log/tag":
                return self._json(api.tag(body.get("attemptId"),
                                          body.get("rootCause"),
                                          body.get("fixNote")))
            if route == "note":
                return self._json(api.save_note(body.get("questionId"),
                                                body.get("body", "")))
            if route == "setting":
                return self._json(api.save_setting(body.get("key"), body.get("value")))

        if method == "DELETE":
            if route == "history":
                return self._json(api.delete_all_history())
            if route.startswith("history/"):
                return self._json(api.delete_session(route.split("/", 1)[1]))

        self._json({"error": f"unknown route {method} /api/{route}"}, 404)


class CatSatServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, api):
        super().__init__(address, handler)
        self.api = api


def free_port(preferred: int = 8756) -> int:
    """Use the preferred port when it's free, otherwise let the OS pick one."""
    for port in (preferred, 0):
        try:
            with socket.socket() as probe:
                probe.bind((HOST, port))
                return probe.getsockname()[1]
        except OSError:
            continue
    return 0


def create_server(port: int | None = None):
    """Build (server, url). Does not start serving."""
    from web_api import Api

    init_db()
    chosen = port if port is not None else free_port()
    server = CatSatServer((HOST, chosen), CatSatHandler, Api())
    return server, f"http://{HOST}:{server.server_address[1]}"


def serve_forever_in_thread(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="catsat-http",
                              daemon=True)
    thread.start()
    return thread


def main():
    server, url = create_server()
    print("=" * 58)
    print("  Cat SAT is running")
    print(f"  Open:  {url}")
    print("  Stop:  Ctrl+C")
    print("=" * 58)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
