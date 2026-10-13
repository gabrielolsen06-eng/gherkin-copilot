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
    drop = login["drop"]  # both sides link to their code, so the two functions can be compared before merging
    assert drop["open_url"].startswith("vscode://file/") and drop["open_url"].endswith(f"/{drop['file']}:{drop['line']}")


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


DIFFERENT = '''from pytest_bdd import parsers, when


@when(parsers.parse('I add "{product}" to the cart'))
def add_to_cart(page, product):
    page.click(product)


@when(parsers.parse('I put the "{product}" in my cart'))
def put_in_cart(page, product):
    page.get_by_text(product).click()
'''


@pytest.fixture
def different(tmp_path):
    """Two steps that say the same thing with different code, so they can only be merged with force."""
    (tmp_path / "steps").mkdir()
    (tmp_path / "features").mkdir()
    (tmp_path / "steps" / "cart_steps.py").write_text(DIFFERENT)
    (tmp_path / "features" / "cart.feature").write_text(
        'Feature: Cart\n  Scenario: Add\n    When I add "Onesie" to the cart\n    And I add "Jacket" to the cart\n\n'
        '  Scenario: Put\n    When I put the "Onesie" in my cart\n')
    return Project("p", "P", str(tmp_path / "steps"), str(tmp_path / "features"))


def test_merge_anyway_when_only_the_code_differs(different):
    cart = pair(dupes.overview(different, 0.6), "add_to_cart")
    assert (cart["ok"], cart["can_force"], cart["rewrites"]) == (False, True, 1)
    assert "page.get_by_text(product).click()" in cart["drop"]["code"]
    assert cart["keep"]["code"].startswith("@when(parsers.parse(") and cart["keep"]["code"].endswith("page.click(product)")
    with pytest.raises(ValueError, match="can't be merged"):
        dupes.preview(different, 0.6, cart["id"], [])
    with pytest.raises(ValueError, match="can't be merged"):
        dupes.preview(different, 0.6, cart["id"], [], force="yes")  # only a real true counts

    preview = dupes.preview(different, 0.6, cart["id"], [], force=True)
    assert '+    When I add "Onesie" to the cart' in preview["files"][0]["diff"]
    with pytest.raises(ValueError, match="can't be merged"):
        dupes.apply(different, 0.6, cart["id"], [], preview["hash"])  # saving needs force too
    dupes.apply(different, 0.6, cart["id"], [], preview["hash"], force=True)
    code = (Path(different.steps) / "cart_steps.py").read_text()
    assert "def put_in_cart" not in code and "page.click(product)" in code
    assert "put the" not in (Path(different.features) / "cart.feature").read_text()


def test_merge_anyway_is_refused_for_other_problems(different):
    (Path(different.steps) / "helpers.py").write_text("from cart_steps import put_in_cart\n")
    cart = pair(dupes.overview(different, 0.6), "add_to_cart")
    assert not cart["can_force"] and cart["keep"]["code"] is None
    with pytest.raises(ValueError, match="used elsewhere"):
        dupes.preview(different, 0.6, cart["id"], [], force=True)


def test_merge_anyway_over_http(different, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("P", different.steps, different.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    try:
        cart = pair(json.loads(request(port, "GET", f"/api/dupes?project={project.id}&threshold=0.6")[2]), "add_to_cart")
        body = {"project": project.id, "threshold": 0.6, "pair": cart["id"], "keep": []}
        assert request(port, "POST", "/api/merge/preview", body, app.token)[0] == 400
        preview = json.loads(request(port, "POST", "/api/merge/preview", {**body, "force": True}, app.token)[2])
        status, _, saved, _ = request(port, "POST", "/api/merge/save",
                                      {**body, "force": True, "hash": preview["hash"], "label": "forced"}, app.token)
        assert status == 200 and json.loads(saved)["saved"] == ["cart.feature", "cart_steps.py"]
    finally:
        server.shutdown()
        server.server_close()
