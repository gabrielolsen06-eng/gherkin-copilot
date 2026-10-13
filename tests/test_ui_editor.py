import json
import shutil
from pathlib import Path

import pytest
from test_ui import request

from gherkin_copilot import gherkin
from gherkin_copilot.ui import builder, editor, history
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background

DEMO = Path(__file__).parent.parent / "demo"
OUTLINE = "Rejected logins show a helpful error"

TRICKY = '''@shop
Feature: Shop
  Free text about the shop.

  # a comment between scenarios
  @smoke @fast
  Scenario: With a table
    Given these users:
      | name  | role  |
      | alice | admin |
    When I post:
      """
      line one
        indented | not a table
      """
    Then it works

  Rule: Admins
    Example: Inside a rule
      Given I am an admin
'''


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".gherkin-copilot"))
    return Project("demo", "Demo", str(root), str(root / "features"))


def feature_text(demo, name):
    return (Path(demo.features) / name).read_text()


# --- parsing and writing -------------------------------------------------------

def test_parse_keeps_spans_tags_tables_and_doc_strings():
    feature = gherkin.parse(TRICKY)
    assert (feature.name, feature.tags, feature.description) == ("Shop", ["@shop"], ["Free text about the shop."])
    table, rule = feature.blocks
    assert (table.start, table.end, table.tags) == (6, 16, ["@smoke", "@fast"])
    assert table.steps[0].attachment == ["  | name  | role  |", "  | alice | admin |"]
    assert table.steps[1].attachment[2] == "    indented | not a table"
    assert (rule.kind, rule.name, rule.indent, rule.start) == ("Example", "Inside a rule", "    ", 19)


def test_every_block_writes_back_unchanged():
    for text in [TRICKY, *(p.read_text() for p in DEMO.rglob("*.feature") if "login" not in p.name)]:
        out = text
        for block in reversed(gherkin.parse(text).blocks):
            out = gherkin.replace_span(out, block.start, block.end, gherkin.render(block))
        assert out == text


def test_table_cells_with_escaped_pipes():
    assert gherkin.cells(r"| a \| b | c |") == ["a | b", "c"]
    assert gherkin.render_table(["x"], [["a|b"]], "") == [r"| x    |", r"| a\|b |"]


# --- listing and opening -------------------------------------------------------

def test_list_scenarios_with_status(demo):
    files = {f["file"]: f for f in editor.list_scenarios(demo)["files"]}
    cart = files["cart.feature"]["blocks"]
    assert [(b["kind"], b["name"], b["missing"]) for b in cart] == [
        ("Background", "", 0), ("Scenario", "Add two products", 0), ("Scenario", "Remove a product again", 0)]
    outline = files["login.feature"]["blocks"][2]
    assert (outline["kind"], outline["examples"]) == ("Scenario Outline", 2)


def test_open_turns_lines_into_blocks_with_values(demo):
    data = editor.open_scenario(demo, "login.feature", 16, OUTLINE)
    log_in = data["steps"][1]
    assert (log_in["keyword"], log_in["step"]["function"]) == ("when", "log_in")
    assert log_in["values"] == {"username": "<username>", "password": "<password>"}
    assert data["examples"][0]["header"] == ["username", "password", "error"]
    with pytest.raises(ValueError, match="moved or changed"):
        editor.open_scenario(demo, "login.feature", 16, "Some other name")


def test_lines_without_a_step_open_as_text(demo):
    (Path(demo.features) / "new.feature").write_text("Feature: n\n  Scenario: s\n    Given something brand new\n")
    step = editor.open_scenario(demo, "new.feature", 2, "s")["steps"][0]
    assert step["step"] is None and step["text"] == "something brand new"


# --- validating specs ------------------------------------------------------------

def spec(**overrides):
    base = {"kind": "Scenario", "name": "Buy", "tags": "@smoke", "description": "",
            "steps": [{"line": 'Given I am logged in as "standard_user"'}], "examples": []}
    return {**base, **overrides}


@pytest.mark.parametrize("bad, message", [
    ({"kind": "Rule"}, "Unknown kind"),
    ({"name": " "}, "name"),
    ({"tags": "smoke @a@b"}, "Tags are single words"),
    ({"description": "Given sneaky step"}, "can't start like a step"),
    ({"description": "| a | table |"}, "can't start like"),
    ({"steps": []}, "at least one step"),
    ({"kind": "Scenario Outline"}, "needs an Examples table"),
    ({"examples": [{"header": ["a"], "rows": [["1"]]}]}, "Only a Scenario Outline"),
    ({"kind": "Scenario Outline", "examples": [{"header": ["a", "a"], "rows": []}]}, "different, non-empty"),
    ({"steps": [{"line": "Given x", "attachment": ["Then smuggled step"]}]}, "data table or a doc string"),
])
def test_bad_specs_are_refused(bad, message):
    with pytest.raises(ValueError, match=message):
        editor.clean_spec(spec(**bad))


def test_tags_get_an_at_sign_and_lose_duplicates():
    assert editor.clean_spec(spec(tags="smoke @smoke wip")).tags == ["@smoke", "@wip"]


# --- edit, delete, duplicate, restore ----------------------------------------------------

def outline_spec():
    return spec(kind="Scenario Outline", name=OUTLINE + " (edited)", tags="@login", steps=[
        {"line": "Given I am on the login page"},
        {"line": 'When I log in as "<username>" with password "<password>"'},
        {"line": 'Then I see the error "<error>"'},
    ], examples=[{"name": "", "header": ["username", "password", "error"],
                  "rows": [["locked_out_user", "secret_sauce", "Sorry"], ["bob", "x", "Nope"]]}])


def test_edit_replaces_only_that_scenario(demo):
    before_text = feature_text(demo, "login.feature")
    change, before, after = editor.plan_edit(demo, "login.feature", 16, OUTLINE, outline_spec())
    builder.apply_change(change, change.hash)
    text = feature_text(demo, "login.feature")
    assert text.startswith(before_text[: before_text.index("  Scenario Outline")])  # earlier lines untouched
    assert "  @login\n  Scenario Outline: Rejected logins show a helpful error (edited)\n" in text
    assert "      | bob             | x            | Nope  |\n" in text
    assert before.startswith("  Scenario Outline: " + OUTLINE) and after.startswith("  @login")
    assert [r["status"] for r in builder.check(demo, [s["line"] for s in outline_spec()["steps"]],
                                               outline_spec()["examples"])] == ["ok"] * 3


def test_edit_refuses_if_the_scenario_moved(demo):
    change, *_ = editor.plan_edit(demo, "login.feature", 16, OUTLINE, outline_spec())
    path = Path(demo.features) / "login.feature"
    path.write_text("# new first line\n" + path.read_text())
    with pytest.raises(ValueError, match="changed since the preview"):
        builder.apply_change(change, change.hash)
    with pytest.raises(ValueError, match="moved or changed"):
        editor.plan_edit(demo, "login.feature", 16, OUTLINE, outline_spec())


def test_background_stays_a_background(demo):
    with pytest.raises(ValueError, match="can't become"):
        editor.plan_edit(demo, "cart.feature", 4, "", spec())
    _change, _, after = editor.plan_edit(demo, "cart.feature", 4, "", spec(kind="Background", name="", tags=""))
    assert after == '  Background:\n    Given I am logged in as "standard_user"\n'


def test_delete_and_duplicate(demo):
    change, _before = editor.plan_delete(demo, "cart.feature", 14, "Remove a product again")
    builder.apply_change(change, change.hash)
    text = feature_text(demo, "cart.feature")
    assert "Remove a product again" not in text and text.endswith("    Then the cart contains 2 items\n")
    change, rendered = editor.plan_duplicate(demo, "cart.feature", 7, "Add two products")
    builder.apply_change(change, change.hash)
    names = [b.name for b in gherkin.parse(feature_text(demo, "cart.feature")).blocks]
    assert names == ["", "Add two products", "Add two products (copy)"]
    change, rendered = editor.plan_duplicate(demo, "cart.feature", 7, "Add two products")
    assert "Add two products (copy 2)" in rendered


def test_restore_undoes_edit_delete_and_add(demo):
    original = feature_text(demo, "cart.feature")
    change, before = editor.plan_delete(demo, "cart.feature", 14, "Remove a product again")
    builder.apply_change(change, change.hash)
    entry = history.record(demo, "delete", "Ann", {change.shown: (change.before, change.after)},
                           file=change.shown, before=before)
    change, *_ = editor.plan_restore(demo, entry)
    builder.apply_change(change, change.hash)
    assert feature_text(demo, "cart.feature") == original

    change, rendered = editor.plan_duplicate(demo, "cart.feature", 7, "Add two products")
    builder.apply_change(change, change.hash)
    entry = history.record(demo, "duplicate", "Ann", {}, file=change.shown, after=rendered)
    change, *_ = editor.plan_restore(demo, entry)
    builder.apply_change(change, change.hash)
    assert feature_text(demo, "cart.feature") == original

    with pytest.raises(ValueError, match="Only changes to a single scenario"):
        editor.plan_restore(demo, {"file": "cart.feature", "before": None, "after": None})


# --- history ------------------------------------------------------------------------

def test_history_lives_in_the_project_and_survives_bad_lines(demo):
    path = history.history_file(demo)
    assert path == Path(demo.steps) / ".gherkin-copilot" / "history.jsonl"
    history.record(demo, "edit", "  Ann   Lee ", {"a.feature": ("x\n", "y\n")}, scenario="S", file="a.feature")
    with path.open("a") as fh:
        fh.write("not json\n")
    history.record(demo, "delete", 42, {}, file="a.feature")
    first, second = history.entries(demo)
    assert (first["action"], first["author"], second["author"]) == ("delete", "", "Ann Lee")
    assert second["diff"][-2:] == ["-x", "+y"]
    assert history.find(demo, second["id"]) == second


def test_unrelated_folders_keep_history_with_the_features(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = Project("p", "P", str(tmp_path / "a"), str(tmp_path / "b"))
    assert history.history_file(project).parent.parent == tmp_path / "b"


# --- over HTTP: every save is recorded ---------------------------------------------------

def test_edit_over_http_records_history_and_restores(demo, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("Demo", demo.steps, demo.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    def post(path, body):
        return json.loads(request(port, "POST", path, {"project": project.id, **body}, app.token)[2])
    try:
        listed = json.loads(request(port, "GET", f"/api/scenarios?project={project.id}")[2])
        assert [f["file"] for f in listed["files"]] == ["cart.feature", "login.feature"]
        opened = json.loads(request(port, "GET", f"/api/scenario/open?project={project.id}&file=login.feature"
                                                 f"&start=16&name={OUTLINE.replace(' ', '%20')}")[2])
        assert opened["kind"] == "Scenario Outline"

        body = {"file": "login.feature", "start": 16, "name": OUTLINE, "spec": outline_spec(), "author": "Ann"}
        preview = post("/api/scenario/edit/preview", body)
        assert post("/api/scenario/edit/save", {**body, "hash": preview["hash"]}) == {"saved": "login.feature"}

        entries = json.loads(request(port, "GET", f"/api/history?project={project.id}")[2])["entries"]
        assert (entries[0]["action"], entries[0]["author"], entries[0]["scenario"]) == ("edit", "Ann", OUTLINE + " (edited)")

        restore = post("/api/history/restore/preview", {"id": entries[0]["id"]})
        post("/api/history/restore/save", {"id": entries[0]["id"], "hash": restore["hash"], "author": "Ann"})
        assert feature_text(demo, "login.feature") == (DEMO / "features/login.feature").read_text()

        status, _, _data, _ = request(port, "GET", f"/api/scenario/open?project={project.id}&file=../x.feature&start=1&name=x")
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_saving_without_changes_is_refused(demo, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    project = store.add("Demo", demo.steps, demo.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    try:
        opened = editor.open_scenario(demo, "cart.feature", 14, "Remove a product again")
        same = spec(name=opened["name"], tags="", steps=[{"line": f"{s['written']} {s['text']}"} for s in opened["steps"]])
        body = {"project": project.id, "file": "cart.feature", "start": 14, "name": opened["name"], "spec": same}
        status, _, data, _ = request(port, "POST", "/api/scenario/edit/preview", body, app.token)
        assert status == 400 and "nothing to save" in json.loads(data)["error"]
        assert history.entries(demo) == []
    finally:
        server.shutdown()
        server.server_close()
