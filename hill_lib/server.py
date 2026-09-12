"""`hill serve` — the floor board, running.

A saved HTML file answers "what did the floor look like when I built this".
A factory floor needs the other question answered: what is happening now. So
this serves the same template from memory and refreshes it on a timer.

Three properties it does not give up:

1. **A request never waits on a collection.** One pass over every repository
   takes about two minutes. A collector thread does that on its own schedule
   and the handler serves whatever it last finished, immediately. A board that
   hangs for two minutes is not a board.

2. **The page always says how old it is.** The age travels with the data, and
   the strip turns amber when refreshes stop landing. Stale numbers presented
   as current are the failure this whole tool exists to avoid; a wall display
   that quietly freezes is the worst version of it.

3. **It serves exactly three things.** The page, the snapshot, a health probe.
   There is no file serving and no path handling, so no request can reach the
   disk. It binds to loopback unless told otherwise, because this reports on
   private repositories and nothing in it should be reachable from a network
   by default.
"""
from __future__ import annotations

import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import build, collect

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = build.PLACEHOLDER


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Board:
    """The newest finished snapshot, and the truth about how it is going."""

    def __init__(self, interval: int) -> None:
        self.interval = max(60, interval)
        self._lock = threading.Lock()
        self._snapshot: dict | None = None
        self._collected_at: datetime.datetime | None = None
        self._started_at: datetime.datetime | None = None
        self._collecting = False
        self._error: str | None = None
        self._stop = threading.Event()

    def state(self) -> dict:
        with self._lock:
            # Age from when the pass STARTED reading, not when it finished
            # storing. collect() stamps its own collected_at on entry, and a
            # pass takes roughly a hundred seconds, so measuring from the
            # finish time makes the data look a hundred seconds fresher than
            # it is -- and disagrees with the timestamp the masthead prints.
            # An age that flatters itself is the one failure this board must
            # not have, so it anchors on the oldest defensible moment.
            anchor = self._started_at or self._collected_at
            age = (_now() - anchor).total_seconds() if anchor else None
            return {
                "snapshot": self._snapshot,
                "collected_at": anchor.isoformat() if anchor else None,
                "stored_at": self._collected_at.isoformat() if self._collected_at else None,
                "age_seconds": age,
                "collecting": self._collecting,
                "interval_seconds": self.interval,
                # Reported, not hidden: a board whose refreshes are failing must
                # say so rather than keep showing the last good numbers as if
                # they were current.
                "last_error": self._error,
            }

    def refresh_once(self) -> None:
        with self._lock:
            self._collecting = True
        try:
            snap = collect.collect()
        except Exception as exc:  # a failed pass must not kill the thread
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:200]
                self._collecting = False
            return
        stamped = snap.get("collected_at") if isinstance(snap, dict) else None
        started = None
        if isinstance(stamped, str):
            try:
                started = datetime.datetime.fromisoformat(stamped)
            except ValueError:
                started = None
        with self._lock:
            self._snapshot = snap
            self._collected_at = _now()
            self._started_at = started
            self._error = None
            self._collecting = False

    def run(self) -> None:
        # The interval is the time between the STARTS of two passes, which is
        # what someone setting --interval 300 means by it. Waiting the full
        # interval after each pass finished instead made the real period
        # interval + collection time: measured at 408s for a 300s setting,
        # because a pass takes about 108s. Small, and in the wrong direction --
        # the board refreshed less often than it said it did.
        #
        # A pass slower than the interval simply starts the next one straight
        # away rather than queueing up passes behind each other.
        while not self._stop.is_set():
            started = _now()
            self.refresh_once()
            elapsed = (_now() - started).total_seconds()
            self._stop.wait(max(0.0, self.interval - elapsed))

    def stop(self) -> None:
        self._stop.set()


class Handler(BaseHTTPRequestHandler):
    server_version = "hill"
    sys_version = ""
    board: Board = None  # set on the server instance
    page: str = ""

    def log_message(self, fmt, *args):  # quiet; the console is for the operator
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # This page renders only its own data and loads nothing remote except
        # the font stylesheet the template already asks for.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        if route == "/":
            self._send(200, self.page.encode(), "text/html; charset=utf-8")
        elif route == "/api/board.json":
            state = self.board.state()
            code = 200 if state["snapshot"] else 503
            body = json.dumps(state, default=str).encode()
            self._send(code, body, "application/json")
        elif route == "/healthz":
            ok = self.board.state()["snapshot"] is not None
            self._send(200 if ok else 503, b"ok\n" if ok else b"collecting\n", "text/plain")
        else:
            # No path is ever turned into a filesystem lookup.
            self._send(404, b"not found\n", "text/plain")


def serve(host: str = "127.0.0.1", port: int = 8787, interval: int = 300) -> None:
    page = TEMPLATE.read_text()
    if PLACEHOLDER not in page:
        raise RuntimeError(f"{TEMPLATE} has no {PLACEHOLDER} to fill")
    # Served with no data inlined: that is what tells the page to fetch and
    # repaint instead of rendering once.
    page = page.replace(PLACEHOLDER, "null")

    # Bind before starting the collector: a failed bind used to surface as a
    # bare OSError traceback *after* a two-minute collection had already begun,
    # which reads as a crash rather than "that port is taken".
    Handler.board = None
    Handler.page = page
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        import errno
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"port {port} is already in use — another board is probably "
                f"running there.\n"
                f"  check it:  curl -s http://127.0.0.1:{port}/healthz\n"
                f"  or pick another:  hill serve --port {port + 1}"
            ) from None
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise SystemExit(
                f"not allowed to bind port {port}; ports below 1024 need "
                f"privileges — try a higher one"
            ) from None
        raise

    board = Board(interval)
    Handler.board = board
    threading.Thread(target=board.run, daemon=True, name="hill-collector").start()
    shown = "localhost" if host in ("127.0.0.1", "::1") else host
    # flush=True throughout: started under nohup or a supervisor, stdout is a
    # pipe and Python buffers it, so the operator sees nothing at all -- not
    # even the URL they need -- until the process exits.
    say = lambda line: print(line, flush=True)
    say(f"the-hill board on http://{shown}:{port}  (refresh every {board.interval}s)")
    if host not in ("127.0.0.1", "::1"):
        say("  bound beyond loopback — this exposes private repository state on your network")
    say("  first collection is running; the page will say so until it lands")
    say("  ctrl-c to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        say("\nstopping")
    finally:
        board.stop()
        httpd.server_close()
