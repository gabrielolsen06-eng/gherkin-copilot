import http.client
import json
from pathlib import Path

import pytest

from gherkin_copilot.indexer import StepDef
from gherkin_copilot.ui.catalog import segments
from gherkin_copilot.ui.projects import ProjectStore
from gherkin_copilot.ui.server import start_in_background

DEMO = Path(__file__).parent.parent / "demo"


@pytest.fixture
def app(tmp_path):
    server, app, _url = start_in_background(ProjectStore(tmp_path / "projects.json"))
    yield app, server.server_address[1]
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, token=None, host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        sent["Content-Type"] = "application/json"
    if token:
        sent["X-Gherkin-Token"] = token
    conn.request(method, path, json.dumps(body) if body is not None else None, sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, response.getheader("Content-Type", ""), data, response


def test_page_carries_the_token_and_strict_headers(app):
    app, port = app
    status, content_type, data, response = request(port, "GET", "/")
    assert status == 200 and content_type.startswith("text/html")
    assert app.token.encode() in data
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert request(port, "GET", "/static/app.js")[0] == 200


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/index.html", "/static/", "/etc/passwd"])
def test_only_listed_files_are_served(app, path):
    _, port = app
    assert request(port, "GET", path)[0] == 404


def test_requests_for_another_host_are_refused(app):
    _, port = app
    assert request(port, "GET", "/api/projects", host="evil.example")[0] == 403


def test_changes_need_the_token_and_matching_origin(app):
    app, port = app
    body = {"name": "Demo", "steps": str(DEMO)}
    assert request(port, "POST", "/api/projects/add", body)[0] == 403
    assert request(port, "POST", "/api/projects/add", body, token="wrong")[0] == 403
    assert request(port, "POST", "/api/projects/add", body, token=app.token,
                   headers={"Origin": "https://evil.example"})[0] == 403
    status, _, data, _ = request(port, "POST", "/api/projects/add", body, token=app.token,
                                 headers={"Origin": f"http://127.0.0.1:{port}"})
    assert status == 200 and json.loads(data)["active"] == "demo"


def test_add_switch_list_steps_and_remove(app, tmp_path):
    app, port = app
    other = tmp_path / "other"
    other.mkdir()
    def add(name, steps):
        return request(port, "POST", "/api/projects/add", {"name": name, "steps": steps}, app.token)

    assert add("Demo shop", str(DEMO))[0] == 200
    _, _, data, _ = add("Demo shop", str(other))  # same name gets its own id
    assert json.loads(data)["active"] == "demo-shop-2"

    _status, _, data, _ = request(port, "GET", "/api/steps?project=demo-shop")
    steps = json.loads(data)["steps"]
    assert len(steps) == 15
    assert request(port, "GET", "/api/steps?project=demo-shop-2")[2] == b'{"steps": []}'

    request(port, "POST", "/api/projects/active", {"id": "demo-shop"}, app.token)
    assert json.loads(request(port, "GET", "/api/projects")[2])["active"] == "demo-shop"
    request(port, "POST", "/api/projects/remove", {"id": "demo-shop"}, app.token)
    data = json.loads(request(port, "GET", "/api/projects")[2])
    assert [p["id"] for p in data["projects"]] == ["demo-shop-2"] and data["active"] == "demo-shop-2"
    assert DEMO.is_dir()  # removing a project never touches its files


@pytest.mark.parametrize("body, message", [
    ({"name": "", "steps": str(DEMO)}, "name"),
    ({"name": "x", "steps": "relative/path"}, "full path"),
    ({"name": "x", "steps": str(DEMO / "no-such-folder")}, "doesn't exist"),  # absolute on Windows too
    ({"name": 5, "steps": str(DEMO)}, "must be text"),
])
def test_bad_projects_get_readable_errors(app, body, message):
    app, port = app
    status, _, data, _ = request(port, "POST", "/api/projects/add", body, app.token)
    assert status == 400 and message in json.loads(data)["error"]


def test_store_survives_restart_and_defaults_features_to_steps(tmp_path):
    store = ProjectStore(tmp_path / "nested" / "projects.json")
    project = store.add("Shop", str(DEMO))
    assert project.features == str(DEMO.resolve())
    projects, active = ProjectStore(store.path).load()
    assert projects == [project] and active == "shop"


def step(pattern, kind):
    return StepDef("when", pattern, kind, "fn", "steps.py", 1)


def test_segments_mark_slots_and_their_types():
    assert segments(step('I add "{product}" to the cart', "parse")) == [
        {"text": 'I add "'}, {"slot": "product", "type": "text"}, {"text": '" to the cart'}]
    assert segments(step("the badge shows {count:d}", "parse"))[-1] == {"slot": "count", "type": "number"}
    assert segments(step(r"^the cart has (?P<count>\d+) items?$", "regex")) == [
        {"text": "the cart has "}, {"slot": "count", "type": "number"}, {"text": " items"}]
    assert segments(step("I wait", "string")) == [{"text": "I wait"}]
