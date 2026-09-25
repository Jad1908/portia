"""The HTTP side: the page, and three JSON routes.

    GET  /api/runs            every run's summary, with its note
    GET  /api/runs/<id>       one run's steps
    PUT  /api/runs/<id>/note  save its star, note and tags

Standard library only, bound to 127.0.0.1. It serves one person reading logs
on their own machine, so there is no auth and no concurrency beyond a lock
around the notes file.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from devtools.traces.logs import Library
from devtools.traces.notes import Notes

ASSETS = Path(__file__).parent / "assets"

#: The largest note a PUT may carry. A note is a paragraph, not a file.
MAX_BODY = 256 * 1024


class Viewer:
    """The library and the notes, as the routes use them."""

    def __init__(self, library: Library, notes: Notes):
        self.library = library
        self.notes = notes
        self._lock = threading.Lock()

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            listed = self.library.runs()
        return [{**run, **self.notes.get(run["key"])} for run in listed]

    def steps(self, run_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            return self.library.steps(run_id)

    def save(self, run_id: str, change: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            key = self.library.key(run_id)
            entry = self.library.entries.get(run_id)
        if key is None:
            return None
        title = entry.summary.get("title") if entry and entry.summary else None
        return self.notes.put(key, change, title=title)


def make_handler(viewer: Viewer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            path = self.path.split("?", 1)[0]
            if path == "/api/runs":
                self._json(viewer.runs())
            elif path.startswith("/api/runs/"):
                steps = viewer.steps(path.removeprefix("/api/runs/"))
                if steps is None:
                    self._json({"error": "no such run"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(steps)
            else:
                self._asset("index.html" if path == "/" else path.lstrip("/"))

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not (path.startswith("/api/runs/") and path.endswith("/note")):
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json({"error": "too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            try:
                change = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json({"error": "not JSON"}, HTTPStatus.BAD_REQUEST)
                return
            saved = viewer.save(path.removeprefix("/api/runs/").removesuffix("/note"), change)
            if saved is None:
                self._json({"error": "no such run"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(saved)

        def _asset(self, name: str) -> None:
            target = (ASSETS / name).resolve()
            if not target.is_file() or ASSETS.resolve() not in target.parents:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(target.read_bytes(), f"{kind}; charset=utf-8")

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def _send(self, body: bytes, kind: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            # The page and the logs change under a running server; never serve a stale copy.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def serve(viewer: Viewer, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(viewer))
