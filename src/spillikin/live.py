"""`spillikin live`: a local page that updates as agents edit SQL and open claims.

One process on 127.0.0.1 serves the explorer, the current graph, and a stream of
events. A watcher notices two things: the SQL on disk changing (an agent editing)
and the store changing (a claim, a refusal, a rebuild). It never writes.
"""

from __future__ import annotations

import json
import mimetypes
import queue
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import claims, workflow
from .cli_export import export_payload
from .project import MANIFEST_NAME
from .store import Store

POLL_SECONDS = 0.5
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def find_explorer(start: Path) -> Path | None:
    for base in (Path.cwd(), start, *start.parents):
        candidate = base / "web" / "dist"
        if (candidate / "index.html").is_file():
            return candidate
    return None


class Hub:
    """Holds the latest snapshot and fans it out to every connected page."""

    def __init__(self, root: Path, store: Store):
        self.root, self.store = root, store
        self._listeners: set[queue.Queue[str]] = set()
        self._lock = threading.Lock()
        self._signature: tuple[Any, ...] | None = None
        self.snapshot: dict[str, Any] = {}
        self.graph: str = ""
        self.refresh()

    def _stamp(self) -> tuple[Any, ...]:
        files = [self.root / MANIFEST_NAME, *sorted(self.root.rglob("*.sql"))]
        sql = tuple((f.as_posix(), f.stat().st_mtime_ns) for f in files if f.is_file())
        graph = self.store.path / "graph.json"
        wal = sorted((self.store.path / "wal").glob("*.log")) if (self.store.path / "wal").is_dir() else []
        stored = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in (graph, *wal) if f.is_file())
        return (sql, stored)

    def refresh(self) -> bool:
        stamp = self._stamp()
        if stamp == self._signature:
            return False
        self._signature = stamp
        try:
            drift = workflow.check(self.root, self.store)
            with self.store.read() as conn:
                held = claims.open_claims(conn)
                refused = claims.recent_refusals(conn)
                graph = json.dumps(export_payload(conn), sort_keys=True, separators=(",", ":"))
            snapshot = {"project": self.root.name, "claims": held, "refusals": refused, "drift": drift, "error": None}
        except Exception as exc:  # noqa: BLE001 - a half-saved file must not take the page down
            snapshot = {**self.snapshot, "error": str(exc)}
            graph = self.graph
        snapshot["graph_changed"] = graph != self.graph
        self.graph, self.snapshot = graph, snapshot
        message = f"data: {json.dumps(snapshot, sort_keys=True)}\n\n"
        with self._lock:
            for listener in self._listeners:
                listener.put(message)
        return True

    def subscribe(self) -> queue.Queue[str]:
        listener: queue.Queue[str] = queue.Queue()
        listener.put(f"data: {json.dumps({**self.snapshot, 'graph_changed': False}, sort_keys=True)}\n\n")
        with self._lock:
            self._listeners.add(listener)
        return listener

    def unsubscribe(self, listener: queue.Queue[str]) -> None:
        with self._lock:
            self._listeners.discard(listener)

    def watch(self, stop: threading.Event) -> None:
        while not stop.wait(POLL_SECONDS):
            self.refresh()


def make_handler(hub: Hub, explorer: Path) -> type[BaseHTTPRequestHandler]:
    explorer = explorer.resolve()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: Any) -> None:  # the terminal is for the watcher's own output
            return

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            # A page on another origin could point a hostname at 127.0.0.1; only answer to our own name.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in _LOCAL_HOSTS:
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            path = self.path.split("?", 1)[0]
            if path == "/events":
                return self._events()
            if path == "/graph.json":
                return self._send(HTTPStatus.OK, hub.graph.encode(), "application/json")
            target = (explorer / path.lstrip("/")).resolve()
            if path.endswith("/"):
                target = target / "index.html"
            if not target.is_relative_to(explorer) or not target.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            kind = "application/wasm" if target.suffix == ".wasm" else (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self._send(HTTPStatus.OK, target.read_bytes(), kind)

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            listener = hub.subscribe()
            try:
                while True:
                    try:
                        message = listener.get(timeout=15)
                    except queue.Empty:
                        message = ": keep-alive\n\n"
                    self.wfile.write(message.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                hub.unsubscribe(listener)

    return Handler


def serve(root: Path, store: Store, port: int, explorer: Path | None) -> None:
    explorer = explorer or find_explorer(root)
    if explorer is None or not (explorer / "index.html").is_file():
        raise FileNotFoundError("explorer build not found; run `npm ci && npm run build` in web/, or pass --explorer")
    hub = Hub(root, store)
    stop = threading.Event()
    threading.Thread(target=hub.watch, args=(stop,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub, explorer))
    server.daemon_threads = True
    print(f"watching {root}\nopen http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
