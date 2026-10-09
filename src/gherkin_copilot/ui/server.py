"""The local web app behind `gherkin-copilot ui`.

Listens on 127.0.0.1 only. Pages and data are served to this machine's
browser; anything that changes state is a POST that must carry the session
token from the page, so other websites open in the same browser can't
trigger it. Requests whose Host header isn't this server are refused, which
blocks DNS-rebinding tricks.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gherkin_copilot import __version__
from gherkin_copilot.ui import builder, dupes
from gherkin_copilot.ui.catalog import describe
from gherkin_copilot.ui.projects import Project, ProjectStore

HOST = "127.0.0.1"
MAX_BODY = 1_000_000
STATIC = {  # the only files the app serves
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _static(name: str) -> bytes:
    return resources.files("gherkin_copilot.ui").joinpath("static", name).read_bytes()


class App:
    """State shared by every request: the project list and this session's token."""

    def __init__(self, store: ProjectStore):
        self.store = store
        self.token = secrets.token_urlsafe(32)

    def projects(self) -> dict[str, Any]:
        projects, active = self.store.load()
        return {"active": active, "projects": [p.__dict__ for p in projects]}

    def project(self, project_id: object) -> Project:
        project = self.store.get(project_id) if isinstance(project_id, str) else None
        if project is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "No such project.")
        for folder in (project.steps, project.features):
            if not Path(folder).is_dir():
                raise ApiError(HTTPStatus.NOT_FOUND, f"This project's folder is missing: {folder}")
        return project

    def steps(self, project_id: str) -> dict[str, Any]:
        return {"steps": describe(Path(self.project(project_id).steps))}

    def files(self, project_id: str) -> dict[str, Any]:
        return builder.project_files(self.project(project_id))

    def post(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        def text(key: str) -> str:
            value = body.get(key, "")
            if not isinstance(value, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} must be text.")
            return value

        try:
            if route == "/api/projects/add":
                project = self.store.add(text("name"), text("steps"), text("features"))
                return {**self.projects(), "added": project.id}
            if route == "/api/projects/remove":
                self.store.remove(text("id"))
                return self.projects()
            if route == "/api/projects/active":
                self.store.set_active(text("id"))
                return self.projects()
            if route == "/api/check":
                return {"results": builder.check(self.project(body.get("project")), builder.clean_lines(body.get("lines")))}
            if route in {"/api/scenario/preview", "/api/scenario/save"}:
                change = builder.plan_scenario(self.project(body.get("project")), text("mode"), text("file"),
                                               text("feature"), text("scenario"), body.get("lines"))
                return self._preview_or_save(route, change, body)
            if route == "/api/merge/preview":
                return dupes.preview(self.project(body.get("project")), body.get("threshold"), text("pair"),
                                     body.get("keep"))
            if route == "/api/merge/save":
                files = dupes.apply(self.project(body.get("project")), body.get("threshold"), text("pair"),
                                    body.get("keep"), body.get("hash"))
                return {"saved": files}
            if route in {"/api/stubs/preview", "/api/stubs/save"}:
                change = builder.plan_stubs(self.project(body.get("project")), text("file"), body.get("lines"),
                                            body.get("only"))
                return self._preview_or_save(route, change, body)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown action.")

    @staticmethod
    def _preview_or_save(route: str, change: builder.FileChange, body: dict[str, Any]) -> dict[str, Any]:
        if route.endswith("/preview"):
            return change.describe()
        expected = body.get("hash")
        if not isinstance(expected, str):
            raise ValueError("Preview the change before saving it.")
        builder.apply_change(change, expected)
        return {"saved": change.shown}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"gherkin-copilot/{__version__}"
        sys_version = ""

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"{HOST}:{port}", f"localhost:{port}"}

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, data: Any) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Wrong host."})
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    page = _static("index.html").decode().replace("{{TOKEN}}", app.token)
                    return self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
                if url.path.startswith("/static/") and url.path[8:] in STATIC:
                    name = url.path[8:]
                    return self._send(HTTPStatus.OK, _static(name), STATIC[name])
                if url.path == "/api/projects":
                    return self._json(HTTPStatus.OK, app.projects())
                project_id = parse_qs(url.query).get("project", [""])[0]
                if url.path == "/api/steps":
                    return self._json(HTTPStatus.OK, app.steps(project_id))
                if url.path == "/api/files":
                    return self._json(HTTPStatus.OK, app.files(project_id))
                if url.path == "/api/dupes":
                    query = parse_qs(url.query)
                    return self._json(HTTPStatus.OK, dupes.overview(
                        app.project(project_id), query.get("threshold", ["0.6"])[0], query.get("keep", [])))
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._host_ok():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Wrong host."})
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{self.headers['Host']}":
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Wrong origin."})
            if not hmac.compare_digest(self.headers.get("X-Gherkin-Token", ""), app.token):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Missing or wrong session token. Reload the page."})
            if self.headers.get_content_type() != "application/json":
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send JSON."})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request too large."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Send a JSON object.")
                self._json(HTTPStatus.OK, app.post(urlsplit(self.path).path, body))
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # keep the terminal quiet
            pass

    return Handler


def make_server(store: ProjectStore, port: int = 0) -> tuple[ThreadingHTTPServer, App]:
    app = App(store)
    return ThreadingHTTPServer((HOST, port), make_handler(app)), app


def start_in_background(store: ProjectStore, port: int = 0) -> tuple[ThreadingHTTPServer, App, str]:
    server, app = make_server(store, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, app, f"http://{HOST}:{server.server_address[1]}"
