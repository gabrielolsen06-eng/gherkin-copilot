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
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from gherkin_copilot import __version__, gherkin
from gherkin_copilot.ui import builder, dupes, editor, history, home, runner, stepcode
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
    "img-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
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
        self.runner = runner.Runner()

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
            if route == "/api/projects/tests":
                if text("test_command").strip():
                    runner.parse_command(text("test_command"))
                self.store.update_tests(text("id"), text("test_command"), text("run_from"),
                                        body.get("capture") is not False)
                return self.projects()

            project = self.project(body.get("project"))
            if route == "/api/run/start":
                return self.runner.start(project, text("scope"), text("file"), text("name"))
            if route == "/api/run/stop":
                self.runner.stop(project.id)
                return {"stopping": True}
            if route == "/api/check":
                lines = builder.clean_lines(body.get("lines"))
                return {"results": builder.check(project, lines, body.get("examples"))}
            if route == "/api/stepcode/draft":
                return {**stepcode.draft(text("line"), text("keyword")), **stepcode.catalogue(),
                        "files": builder.project_files(project)["steps"]}
            if route == "/api/stepcode/code":
                try:
                    return stepcode.code(text("line"), text("keyword"), body.get("names"), body.get("actions"))
                except ValueError as exc:  # shown next to the live preview while the user is still editing
                    return {"code": None, "error": str(exc)}
            base, _, step = route.rpartition("/")
            if step not in {"preview", "save"}:
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown action.")
            author = body.get("author")
            where = (text("file"), body.get("start"), body.get("name"))

            if base == "/api/merge":
                force = body.get("force") is True  # "Merge anyway": the user says the differing code does the same
                if step == "preview":
                    return dupes.preview(project, body.get("threshold"), text("pair"), body.get("keep"), force)
                changes = dupes.apply(project, body.get("threshold"), text("pair"), body.get("keep"), body.get("hash"),
                                      force)
                history.record(project, "merge", author, changes, scenario=text("label"))
                return {"saved": sorted(changes)}
            if base == "/api/stepcode":
                def exists(line: str) -> bool:
                    return bool(builder.check(project, builder.clean_lines([line]))[0]["status"] != "missing")

                change = stepcode.plan(project, text("line"), text("keyword"), body.get("names"), body.get("actions"),
                                       text("file"), body.get("new_file") is True, exists)
                return self._flow(project, step, change, body, "add step code", author)
            if base == "/api/stubs":
                change = builder.plan_stubs(project, text("file"), body.get("lines"), body.get("only"),
                                            body.get("examples"))
                return self._flow(project, step, change, body, "add steps", author)
            if base == "/api/scenario":
                change, rendered = editor.plan_new(project, text("mode"), text("file"), text("feature"),
                                                   body.get("spec"))
                return self._flow(project, step, change, body, "create" if change.creates else "add", author,
                                  scenario=self._name(rendered), after=rendered)
            if base == "/api/scenario/edit":
                change, before, after = editor.plan_edit(project, *where, body.get("spec"))
                return self._flow(project, step, change, body, "edit", author, scenario=self._name(after),
                                  before=before, after=after)
            if base == "/api/scenario/delete":
                change, before = editor.plan_delete(project, *where)
                return self._flow(project, step, change, body, "delete", author, scenario=self._name(before),
                                  before=before)
            if base == "/api/scenario/duplicate":
                change, after = editor.plan_duplicate(project, *where)
                return self._flow(project, step, change, body, "duplicate", author, scenario=self._name(after),
                                  after=after)
            if base == "/api/history/restore":
                entry = history.find(project, text("id"))
                change, earlier, current = editor.plan_restore(project, entry)
                return self._flow(project, step, change, body, "restore", author,
                                  scenario=self._name(current or earlier or ""), before=earlier, after=current)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown action.")

    @staticmethod
    def _name(scenario_text: str) -> str:
        blocks = gherkin.parse("Feature: x\n" + scenario_text).blocks
        return (blocks[0].name or blocks[0].kind) if blocks else ""

    @staticmethod
    def _flow(project: Project, step: str, change: builder.FileChange, body: dict[str, Any], action: str,
              author: object, scenario: str = "", before: str | None = None,
              after: str | None = None) -> dict[str, Any]:
        """Preview a change, or save it (if it still matches the preview) and record it in the history."""
        if change.before == change.after and not change.creates:
            raise ValueError("There's nothing to save: the file would stay exactly the same.")
        if step == "preview":
            return change.describe()
        expected = body.get("hash")
        if not isinstance(expected, str):
            raise ValueError("Preview the change before saving it.")
        builder.apply_change(change, expected)
        history.record(project, action, author, {change.shown: (change.before, change.after)},
                       scenario=scenario, file=change.shown, before=before, after=after)
        return {"saved": change.shown}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"gherkin-copilot/{__version__}"
        sys_version = ""

        def _host_ok(self) -> bool:
            port = cast(tuple[str, int], self.server.server_address)[1]
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
                if url.path == "/api/home":
                    return self._json(HTTPStatus.OK, home.overview(app.project(project_id)))
                if url.path == "/api/steps":
                    return self._json(HTTPStatus.OK, app.steps(project_id))
                if url.path == "/api/files":
                    return self._json(HTTPStatus.OK, app.files(project_id))
                query = parse_qs(url.query)
                if url.path == "/api/scenarios":
                    return self._json(HTTPStatus.OK, editor.list_scenarios(app.project(project_id)))
                if url.path == "/api/scenario/open":
                    start = query.get("start", [""])[0]
                    return self._json(HTTPStatus.OK, editor.open_scenario(
                        app.project(project_id), query.get("file", [""])[0], int(start) if start.isdigit() else None,
                        query.get("name", [""])[0]))
                if url.path == "/api/run":
                    project = app.project(project_id)
                    return self._json(HTTPStatus.OK, {"run": app.runner.status(project.id),
                                                      "results": runner.latest_results(project),
                                                      "default_folder": str(runner.default_folder(project))})
                if url.path == "/api/artifact":
                    body, content_type = runner.artifact(app.project(project_id), query.get("path", [""])[0])
                    return self._send(HTTPStatus.OK, body, content_type)
                if url.path == "/api/history":
                    project = app.project(project_id)
                    return self._json(HTTPStatus.OK, {"entries": history.entries(project),
                                                      "file": str(history.history_file(project))})
                if url.path == "/api/dupes":
                    return self._json(HTTPStatus.OK, dupes.overview(
                        app.project(project_id), query.get("threshold", ["0.6"])[0], query.get("keep", [])))
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

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
