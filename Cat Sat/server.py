"""
server.py, the local HTTP server behind the Cat SAT web UI.

Standard library only: no Flask, no FastAPI, nothing new to install. It binds to
127.0.0.1 on a free port, so it is not reachable from anywhere but this machine.

Why a browser at all: the lag was never the database or the Python. It was
CustomTkinter drawing several hundred hand-rendered widgets per screen on the
one thread that also had to stay responsive. A browser does that work on a
compositor thread, caches images itself, and scrolls a thousand rows without
blinking, which is exactly the shape of this app.

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


def _as_int(value, default: int, low: int | None = None, high: int | None = None) -> int:
    """
    Coerce a JSON value to an int, never raising.

    These came off `int(body.get("limit", 10))`, which turns a missing key, a
    null, or a string into a TypeError/ValueError, and _route turns that into
    a 500 whose body is the raw Python message, which app.js then toasts at the
    student. "int() argument must be a string..." is not a thing to show
    anybody. Clamping also stops a huge value from building a million-row query.
    """
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if low is not None:
        out = max(low, out)
    if high is not None:
        out = min(high, out)
    return out


def _as_float(value, default: float, low: float | None = None,
              high: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:                       # NaN compares false with everything
        return default
    if low is not None:
        out = max(low, out)
    if high is not None:
        out = min(high, out)
    return out


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
        """The JSON request body, always as a dict."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}
        # A JSON body is allowed to be a list, a string or a number. Every
        # caller here does body.get(...), so anything that is not an object
        # would raise AttributeError and surface as a 500 full of Python.
        return data if isinstance(data, dict) else {}

    def _drain(self) -> None:
        """
        Read and discard an unread request body.

        With HTTP/1.1 keep-alive, the connection is reused. Answering without
        consuming Content-Length bytes leaves the body in the socket, and the
        next request gets parsed starting from the middle of it, so the
        FOLLOWING request fails, not this one, and it keeps failing until the
        browser gives up on that connection. Any early return on an upload path
        has to drain first.
        """
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(1 << 20, remaining))
            if not chunk:
                return
            remaining -= len(chunk)

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
            # An exception part way through an upload leaves up to 160 MB
            # unread. Drain before answering or the connection is poisoned for
            # every request after this one.
            try:
                self._drain()
            except Exception:                             # noqa: BLE001
                pass
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ----------------------------------------------------------------- setup

    def _serve_setup(self, method, action, query):
        """First-run setup: environment, PDF upload, import, progress.

        Upload takes the file as the RAW request body with ?name=... rather
        than multipart. A question-bank export is 100-160 MB; multipart would
        mean parsing and buffering all of it, while a raw body streams straight
        to disk in 1 MB chunks. The browser sends a File object as-is, so the
        client side is one line.
        """
        import setup_api

        if method == "GET":
            if action == "profile":
                return self._json(setup_api.saved_profile())
            if action == "env":
                return self._json(setup_api.environment())
            if action == "progress":
                return self._json(setup_api.status())
            if action == "migrate/progress":
                import migration
                return self._json(migration.status())

        if method == "POST":
            if action == "upload":
                name = query.get("name") or "upload.pdf"
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if not length:
                    return self._json({"error": "Empty upload."}, 400)
                result = setup_api.save_upload(name, self.rfile, length)
                if result.get("error"):
                    self._drain()      # rejected before reading (e.g. not a PDF)
                return self._json(result)
            if action == "import":
                body = self._body()
                return self._json(setup_api.start_import(force=bool(body.get("force"))))
            if action == "backup":
                self._body()          # drain it, or the next request desyncs
                return self._json(setup_api.make_backup())
            if action == "migrate/scan":
                import migration
                return self._json(migration.scan(str(self._body().get("path") or "")))
            if action == "migrate/start":
                import migration
                body = self._body()
                return self._json(migration.start(str(body.get("path") or ""),
                                                  skip_pdfs=bool(body.get("skipPdfs"))))
            if action == "profile/upload":
                name = query.get("name") or "report.pdf"
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if not length:
                    return self._json({"error": "Empty upload."}, 400)
                result = setup_api.save_report_upload(name, self.rfile, length)
                if result.get("error"):
                    self._drain()
                return self._json(result)
            if action == "profile/save":
                return self._json(setup_api.save_profile(self._body()))

        # Nothing above consumed the body on this path, and with keep-alive an
        # unread body becomes the next request's first line.
        self._drain()
        return self._json({"error": f"Unknown setup action: {action}"}, 404)

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
        # Never cache the app's own files. They come from localhost and are
        # ~100 KB, so caching saves nothing measurable — and it costs real
        # confusion: after an update the browser kept serving a stale app.css
        # for an hour, so a fix that WAS installed looked like it had not
        # worked. Question images are different (see _serve_image): they are
        # named by question id, never change, and there are thousands of them.
        cache = "no-cache, no-store, must-revalidate"
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
        # Setup runs before there is a question bank, so it is handled ahead of
        # everything else and never touches `api`, which assumes a usable bank.
        if route.startswith("setup/"):
            return self._serve_setup(method, route[len("setup/"):], query)

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
            if route == "tests":
                return self._json(api.practice_tests())
            if route == "skills":
                return self._json(api.skill_status())

        # --- writes
        if method == "POST":
            if route == "start/test":
                return self._json(api.start_test(
                    mode=body.get("mode", "section"),
                    sections=body.get("sections") or [],
                    timed=bool(body.get("timed", True)),
                    threshold=_as_float(body.get("threshold"), 0.65, 0.05, 0.95),
                    weighted=bool(body.get("weighted", True))))
            if route == "start/drill":
                return self._json(api.start_drill(
                    section=body.get("section"),
                    domains=body.get("domains") or [],
                    count=_as_int(body.get("count"), 8, 1, 100),
                    difficulty=body.get("difficulty") or None,
                    ramp=bool(body.get("ramp", True)),
                    timer=body.get("timer", "Per-question stopwatch"),
                    source=body.get("source", "fresh")))
            if route == "start/redo":
                return self._json(api.start_redo(_as_int(body.get("limit"), 10, 1, 200)))
            if route == "tests/build":
                return self._json(api.build_practice_tests())
            if route == "skills/recover":
                return self._json(api.recover_skills())
            if route == "start/practice":
                return self._json(api.start_practice_test(
                    number=_as_int(body.get("number"), 1, 1, 10_000),
                    sections=body.get("sections") or None,
                    timed=bool(body.get("timed", True)),
                    threshold=_as_float(body.get("threshold"), 0.65, 0.05, 0.95),
                    weighted=bool(body.get("weighted", True))))
            if route == "start/check":
                return self._json(api.start_check(
                    section=body.get("section"),
                    domains=body.get("domains") or [],
                    count=_as_int(body.get("count"), 10, 1, 100),
                    difficulty=body.get("difficulty") or None,
                    ramp=bool(body.get("ramp", True))))
            if route == "check":
                return self._json(api.check_answer(
                    _as_int(body.get("index"), -1, -1, 10_000),
                    str(body.get("answer") or "")))
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
                return self._json(api.retire_target(
                    _as_int(body.get("targetId"), 0, 0),
                    _as_float(body.get("accuracy"), 0.0, 0.0, 100.0)))
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

    # Close out any sitting left open by a previous run.
    #
    # The desktop app did this at startup and the web port did not, so every
    # test you walked away from stayed 'in_progress' forever. It is easier to
    # do than it sounds: finish Module 1, land on the break screen, and click a
    # nav tab. The break screen has already set State.quiz = null, so the
    # "leave the sitting?" guard does not fire, nothing calls /api/abandon, and
    # the session row is orphaned with its 27 answered questions showing as
    # 0/0. They accumulate one per abandoned test, clutter the history list,
    # and are excluded from the dashboard timeline because that filters on
    # status = 'completed'.
    #
    # Startup is the right moment: nothing can legitimately be in progress
    # before the server is serving.
    from attempt_repo import abandon_stale_sessions
    closed = abandon_stale_sessions()
    if closed:
        print(f"  closed {closed} sitting(s) left open by a previous run")

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
