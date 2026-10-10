import json
import shutil
from pathlib import Path

import pytest

from gherkin_copilot.ui import builder, stepcode
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background
from test_ui import request

DEMO = Path(__file__).parent.parent / "demo"
LINE = 'When I enter my details "Ada" "Lovelace" 3'


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".gherkin-copilot"))
    return Project("demo", "Demo", str(root / "steps"), str(root / "features"))


def fill(by, target, value):
    return {"action": "fill", "target": {"by": by, "value": target}, "value": value}


def test_draft_names_the_values():
    info = stepcode.draft(LINE, "when")
    assert info["pattern"] == 'I enter my details "{text1}" "{text2}" {number3:d}'
    assert [p["type"] for p in info["params"]] == ["text", "text", "number"]
    assert info["function"] == "i_enter_my_details"


def test_code_from_actions():
    result = stepcode.code(LINE, "when", {"text1": "first", "text2": "last", "number3": "age"}, [
        fill("label", "First Name", {"param": "first"}),
        fill("placeholder", "Age", {"param": "age"}),
        {"action": "click", "target": {"by": "button", "value": 'Go "now"'}},
        {"action": "expect_count", "target": {"by": "css", "value": ".row"}, "value": {"param": "age"}},
        {"action": "expect_url", "value": {"text": "step-two"}},
    ])
    assert result["code"] == (
        "@when(parsers.parse('I enter my details \"{first}\" \"{last}\" {age:d}'))\n"
        "def i_enter_my_details(page, first, last, age):\n"
        "    page.get_by_label('First Name').fill(first)\n"
        "    page.get_by_placeholder('Age').fill(str(age))\n"
        "    page.get_by_role(\"button\", name='Go \"now\"').click()\n"
        "    expect(page.locator('.row')).to_have_count(age)\n"
        "    expect(page).to_have_url(re.compile(re.escape('step-two')))\n")


@pytest.mark.parametrize("names, actions, message", [
    ({}, [], "at least one action"),
    ({}, [{"action": "os.system"}], "choose what it does"),
    ({}, [{"action": "click", "target": {"by": "xpath", "value": "//a"}}], "how to find the element"),
    ({}, [{"action": "click", "target": {"by": "css", "value": ""}}], "Fill in action 1's element"),
    ({}, [fill("label", "Name", {"param": "nope"})], "isn't one of this step's values"),
    ({}, [fill("label", "Name", {"text": "line\nbreak"})], "one line"),
    ({"text1": "page"}, [{"action": "click", "target": {"by": "css", "value": "a"}}], "can't be a value name"),
    ({"text1": "x); import os; ("}, [{"action": "click", "target": {"by": "css", "value": "a"}}], "can't be a value name"),
    ({"text1": "same", "text2": "same"}, [{"action": "click", "target": {"by": "css", "value": "a"}}], "both called"),
])
def test_bad_actions_are_refused(names, actions, message):
    with pytest.raises(ValueError, match=message):
        stepcode.code(LINE, "when", names, actions)


def test_typed_text_never_becomes_code():
    sneaky = "'); import os; os.system('echo hi'); ('"
    source = stepcode.code("When I log out", "when", {}, [
        {"action": "click", "target": {"by": "css", "value": sneaky}}])["code"]
    assert "import os" not in source.split("locator(")[0]
    assert f"page.locator({sneaky!r}).click()" in source


def test_save_into_an_existing_file_adds_imports(demo):
    exists = lambda line: builder.check(demo, [line])[0]["status"] != "missing"
    actions = [{"action": "click", "target": {"by": "css", "value": ".shopping_cart_link"}},
               {"action": "expect_url", "value": {"text": "cart"}}]
    change = stepcode.plan(demo, "When I open the cart page", "when", {}, actions, "login_steps.py", False, exists)
    builder.apply_change(change, change.hash)
    source = (Path(demo.steps) / "login_steps.py").read_text()
    compile(source, "login_steps.py", "exec")
    assert "\nimport re\n" in source and "@when('I open the cart page')" in source
    assert builder.check(demo, ["When I open the cart page"])[0]["status"] == "ok"
    with pytest.raises(ValueError, match="already exists"):
        stepcode.plan(demo, "When I open the cart page", "when", {}, actions, "login_steps.py", False, exists)


def test_save_into_a_new_file(demo):
    change = stepcode.plan(demo, "Then I see 2 rows", "then", {"number1": "rows"},
                           [{"action": "expect_count", "target": {"by": "css", "value": ".row"}, "value": {"param": "rows"}}],
                           "table_steps.py", True, lambda line: False)
    assert change.creates and change.after.startswith('"""Steps built with')
    assert "from pytest_bdd import parsers, then\nfrom playwright.sync_api import expect\n" in change.after
    compile(change.after, "table_steps.py", "exec")


@pytest.mark.parametrize("file", ["../evil.py", "Bad Name.py", "x.txt", "cart_steps.py", "nofolder/new_steps.py"])
def test_new_file_names_are_checked(demo, file):
    with pytest.raises(ValueError):
        stepcode.plan(demo, "When I log out", "when", {}, [{"action": "click", "target": {"by": "css", "value": "a"}}],
                      file, True, lambda line: False)


def test_build_over_http_runs_with_pytest(demo, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("Demo", demo.steps, demo.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    post = lambda path, body: request(port, "POST", path, {"project": project.id, **body}, app.token)
    try:
        info = json.loads(post("/api/stepcode/draft", {"line": "When I open the cart page", "keyword": "when"})[2])
        assert info["files"] == ["cart_steps.py", "login_steps.py"] and {a["id"] for a in info["actions"]} >= {"click"}
        body = {"line": "When I open the cart page", "keyword": "when", "names": {}, "file": "cart_steps.py",
                "actions": [{"action": "click", "target": {"by": "css", "value": ".shopping_cart_link"}}]}
        live = json.loads(post("/api/stepcode/code", {**body, "actions": []})[2])
        assert live == {"code": None, "error": "Add at least one action."}
        preview = json.loads(post("/api/stepcode/preview", body)[2])
        status, _, data, _ = post("/api/stepcode/save", {**body, "hash": preview["hash"], "author": "Ann"})
        assert status == 200 and json.loads(data) == {"saved": "cart_steps.py"}
        entries = json.loads(request(port, "GET", f"/api/history?project={project.id}")[2])["entries"]
        assert entries[0]["action"] == "add step code"
    finally:
        server.shutdown()
        server.server_close()
