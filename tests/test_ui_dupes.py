import json
import shutil
from pathlib import Path

import pytest
from test_ui import request

from gherkin_copilot.indexer import build_index
from gherkin_copilot.matcher import find_duplicates
from gherkin_copilot.ui import dupes
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background

DEMO = Path(__file__).parent.parent / "demo"


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".gherkin-copilot"))
    return Project("demo", "Demo", str(root), str(root / "features"))


def pair(data, function):
    return next(p for p in data["pairs"] if function in (p["keep"]["function"], p["drop"]["function"]))


def test_overview_lists_pairs_with_usage(demo):
    data = dupes.overview(demo, 0.6)
    login, cart = pair(data, "open_login_page"), pair(data, "add_to_cart")
    assert (login["keep"]["function"], login["keep"]["uses"], login["drop"]["uses"]) == ("open_login_page", 2, 0)
    assert (cart["drop"]["function"], cart["drop"]["uses"], cart["rewrites"]) == ("put_in_cart", 1, 1)
    assert login["ok"] and cart["ok"] and data["skipped"] == []
    assert login["keep"]["segments"] == [{"text": "I am on the login page"}]


def test_threshold_is_clamped(demo):
    assert dupes.overview(demo, "not a number")["threshold"] == 0.6
    assert dupes.overview(demo, 5)["threshold"] == 0.95
    assert dupes.overview(demo, 0.95)["pairs"] == []


def test_keep_this_one_instead(demo):
    swapped = pair(dupes.overview(demo, 0.6, ["user_on_login_page"]), "open_login_page")
    assert (swapped["keep"]["function"], swapped["rewrites"]) == ("user_on_login_page", 2)


def test_preview_then_apply_one_pair(demo):
    cart = pair(dupes.overview(demo, 0.6), "add_to_cart")
    preview = dupes.preview(demo, 0.6, cart["id"], [])
    assert [f["file"] for f in preview["files"]] == ["features/cart.feature", "steps/cart_steps.py"]
    assert '+    When I add "Sauce Labs Onesie" to the cart' in preview["files"][0]["diff"]
    assert "def put_in_cart" in (Path(demo.steps) / "steps/cart_steps.py").read_text()  # preview writes nothing

    assert sorted(dupes.apply(demo, 0.6, cart["id"], [], preview["hash"])) == ["features/cart.feature", "steps/cart_steps.py"]
    remaining = find_duplicates(build_index(Path(demo.steps)))
    assert [p.first.function for p in remaining] == ["open_login_page"]  # the other pair is untouched


def test_apply_refuses_stale_previews_and_blocked_pairs(demo):
    login = pair(dupes.overview(demo, 0.6), "open_login_page")
    preview = dupes.preview(demo, 0.6, login["id"], [])
    steps_file = Path(demo.steps) / "steps/login_steps.py"
    steps_file.write_text(steps_file.read_text() + "\n# edited elsewhere\n")
    with pytest.raises(ValueError, match="changed since the preview"):
        dupes.apply(demo, 0.6, login["id"], [], preview["hash"])
    with pytest.raises(ValueError, match="Preview the merge"):
        dupes.apply(demo, 0.6, login["id"], [], None)
    with pytest.raises(ValueError, match="Refresh"):
        dupes.preview(demo, 0.6, "nope|nope", [])

    steps_file.write_text(steps_file.read_text().replace("def user_on_login_page(page: Page, app_url: str):\n    page.goto(app_url)",
                                                         "def user_on_login_page(page: Page, app_url: str):\n    page.reload()"))
    blocked = pair(dupes.overview(demo, 0.6), "open_login_page")
    assert not blocked["ok"] and "don't do the same thing" in blocked["problems"][0]
    with pytest.raises(ValueError, match="can't be merged"):
        dupes.preview(demo, 0.6, blocked["id"], [])


def test_merge_over_http(demo, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("Demo", demo.steps, demo.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    try:
        data = json.loads(request(port, "GET", f"/api/dupes?project={project.id}&threshold=0.6&keep=user_on_login_page")[2])
        login = pair(data, "open_login_page")
        assert login["keep"]["function"] == "user_on_login_page"
        body = {"project": project.id, "threshold": 0.6, "pair": login["id"], "keep": ["user_on_login_page"]}
        assert request(port, "POST", "/api/merge/save", {**body, "hash": "x"})[0] == 403  # no token
        preview = json.loads(request(port, "POST", "/api/merge/preview", body, app.token)[2])
        status, _, saved, _ = request(port, "POST", "/api/merge/save", {**body, "hash": preview["hash"]}, app.token)
        assert status == 200 and json.loads(saved)["saved"] == ["features/login.feature", "steps/login_steps.py"]
        assert "I am on the login page" not in (Path(demo.features) / "login.feature").read_text()
        status, _, data, _ = request(port, "POST", "/api/merge/preview", {**body, "keep": "not a list"}, app.token)
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
