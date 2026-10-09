import json
import shutil
from pathlib import Path

import pytest

from gherkin_copilot.ui import builder
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background
from test_ui import request

DEMO = Path(__file__).parent.parent / "demo"
DRAFT = [
    'Given I am logged in as "standard_user"',
    'When I add "Sauce Labs Backpack" to the cart',
    "Then the cart badge shows 1",
    "And I open the cart page",
    'And the order total is "$29.99"',
]


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("__pycache__"))
    return Project("demo", "Demo", str(root), str(root / "features"))


def test_check_reports_each_line(demo):
    results = builder.check(demo, DRAFT)
    assert [r["status"] for r in results] == ["ok", "ok", "ok", "missing", "missing"]
    assert results[0]["bound"][0]["function"] == "logged_in_as"
    assert results[3]["stub"].startswith("@then('I open the cart page')")  # "And" follows the Then above it

    suggestion = builder.check(demo, ["Given I open the login page"])[0]["suggestions"][0]
    assert suggestion["function"] == "open_login_page" and suggestion["segments"] == [{"text": "I am on the login page"}]


@pytest.mark.parametrize("lines, message", [
    (["I forgot the keyword"], "must start with Given"),
    ("Given a string, not a list", "list of text lines"),
    (["Given x"] * 201, "at most 200"),
])
def test_bad_drafts_are_refused(lines, message):
    with pytest.raises(ValueError, match=message):
        builder.clean_lines(lines)


def test_newlines_cant_smuggle_extra_lines_into_a_file():
    assert builder.clean_lines(["Given a\n  Scenario: sneaky"]) == ["Given a Scenario: sneaky"]


def test_save_new_feature_file(demo):
    change = builder.plan_scenario(demo, "new", "checkout", "Checkout", "Buy one product", DRAFT)
    assert change.creates and change.shown == "checkout.feature"
    builder.apply_change(change, change.hash)
    text = (Path(demo.features) / "checkout.feature").read_text()
    assert text.startswith("Feature: Checkout\n\n  Scenario: Buy one product\n    Given I am logged in")
    with pytest.raises(ValueError, match="already exists"):
        builder.plan_scenario(demo, "new", "checkout", "Checkout", "Again", DRAFT)


@pytest.mark.parametrize("name", ["../outside", "a/b", "", ".hidden", "x" * 90])
def test_new_file_names_stay_in_the_features_folder(demo, name):
    with pytest.raises(ValueError):
        builder.plan_scenario(demo, "new", name, "F", "S", DRAFT)


def test_append_matches_the_files_indentation(demo, tmp_path):
    feature = Path(demo.features) / "cart.feature"
    change = builder.plan_scenario(demo, "append", "cart.feature", "", "Check out", DRAFT[:1])
    builder.apply_change(change, change.hash)
    assert feature.read_text().endswith(
        '    Then the cart badge is hidden\n\n  Scenario: Check out\n    Given I am logged in as "standard_user"\n')


@pytest.mark.parametrize("file", ["../steps/cart_steps.py", "/etc/passwd", "login.feature/../../conftest.py", "nope.feature"])
def test_append_only_to_feature_files_in_the_project(demo, file):
    with pytest.raises(ValueError):
        builder.plan_scenario(demo, "append", file, "", "S", DRAFT)


def test_save_refuses_if_the_file_changed_since_the_preview(demo):
    change = builder.plan_scenario(demo, "append", "cart.feature", "", "S", DRAFT)
    (Path(demo.features) / "cart.feature").write_text("Feature: edited elsewhere\n")
    with pytest.raises(ValueError, match="changed since the preview"):
        builder.apply_change(change, change.hash)
    with pytest.raises(ValueError, match="changed since the preview"):
        builder.apply_change(change, "0" * 64)


def test_stubs_go_into_a_step_file_with_the_imports_they_need(demo):
    login = Path(demo.steps) / "steps" / "login_steps.py"
    change = builder.plan_stubs(demo, "steps/login_steps.py", DRAFT, only=[3, 4])
    builder.apply_change(change, change.hash)
    source = login.read_text()
    compile(source, str(login), "exec")
    assert "@then('I open the cart page')\ndef i_open_the_cart_page(page):" in source
    assert "@then(parsers.parse('the order total is \"{text1}\"'))" in source
    assert [r["status"] for r in builder.check(demo, DRAFT)] == ["ok"] * 5


def test_stubs_add_missing_imports_and_avoid_name_clashes(tmp_path):
    (tmp_path / "steps.py").write_text(
        "from pytest_bdd import given\n\n\n@given('x')\ndef i_open_the_cart_page():\n    pass\n")
    project = Project("p", "P", str(tmp_path), str(tmp_path))
    change = builder.plan_stubs(project, "steps.py", ["Given x", "When I open the cart page"])
    assert change.after.splitlines()[1] == "from pytest_bdd import when"
    assert "def i_open_the_cart_page_2(page):" in change.after
    compile(change.after, "steps.py", "exec")


@pytest.mark.parametrize("file", ["conftest.py", "../conftest.py", "steps/__init__.py", "/etc/hosts.py"])
def test_stubs_only_go_into_existing_step_files(demo, file):
    with pytest.raises(ValueError):
        builder.plan_stubs(demo, file, DRAFT)


def test_stubs_need_something_missing(demo):
    with pytest.raises(ValueError, match="already exists"):
        builder.plan_stubs(demo, "steps/cart_steps.py", DRAFT[:3])


def test_preview_then_save_over_http(demo, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("Demo", demo.steps, demo.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    try:
        body = {"project": project.id, "mode": "new", "file": "checkout", "feature": "Checkout",
                "scenario": "Buy", "lines": DRAFT}
        status, _, data, _ = request(port, "POST", "/api/scenario/save", body, app.token)
        assert status == 400 and "Preview" in json.loads(data)["error"]
        preview = json.loads(request(port, "POST", "/api/scenario/preview", body, app.token)[2])
        assert preview["creates"] and "+Feature: Checkout" in preview["diff"]
        assert not (Path(demo.features) / "checkout.feature").exists()
        status, _, data, _ = request(port, "POST", "/api/scenario/save", {**body, "hash": preview["hash"]}, app.token)
        assert status == 200 and json.loads(data) == {"saved": "checkout.feature"}

        files = json.loads(request(port, "GET", f"/api/files?project={project.id}")[2])
        assert files == {"features": ["cart.feature", "checkout.feature", "login.feature"],
                         "steps": ["steps/cart_steps.py", "steps/login_steps.py"]}
    finally:
        server.shutdown()
        server.server_close()
