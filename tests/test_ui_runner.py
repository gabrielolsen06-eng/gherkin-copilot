import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_ui import request

from gherkin_copilot.ui import history, records, runner, settings
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background

FEATURE = """Feature: Sums
  Scenario: Adding works
    Given I have 2
    Then I have 2

  Scenario: Adding works again
    Given I have 3
    Then I have 4
"""
STEPS = """from pytest_bdd import given, parsers, scenarios, then

scenarios("../features/sums.feature")


@given(parsers.parse("I have {n:d}"), target_fixture="total")
def have(n):
    return n


@then(parsers.parse("I have {n:d}"))
def check(total, n):
    assert total == n
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "features").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "features" / "sums.feature").write_text(FEATURE)
    (tmp_path / "features" / "other.feature").write_text("Feature: o\n  Scenario: Not loaded\n    Given I have 1\n")
    (tmp_path / "tests" / "test_sums.py").write_text(STEPS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    return Project("p", "P", str(tmp_path), str(tmp_path / "features"), "python -m pytest tests -p no:cacheprovider",
                   str(tmp_path), False)


def wait(run_status: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
    deadline = time.time() + 60
    while (status := run_status()) is not None and status["status"] == "running":
        assert time.time() < deadline, "tests took too long"
        time.sleep(0.1)
    assert status is not None
    return status


@pytest.mark.parametrize("command, expected", [
    ("python -m pytest demo", ({}, [sys.executable, "-m", "pytest", "demo"])),
    ("DEMO_BROWSER=brave A=1 pytest -k 'a b'", ({"DEMO_BROWSER": "brave", "A": "1"}, ["pytest", "-k", "a b"])),
])
def test_parse_command(command, expected):
    assert runner.parse_command(command) == expected


@pytest.mark.parametrize("command, windows, expected", [
    ('pytest -k "a b" --x="c d"', True, ["pytest", "-k", "a b", "--x=c d"]),
    ("pytest -k 'a b'", True, ["pytest", "-k", "a b"]),
    (r'C:\venv\Scripts\python.exe -m pytest "C:\My Tests\cart"', True,
     [r"C:\venv\Scripts\python.exe", "-m", "pytest", r"C:\My Tests\cart"]),
    (r"pytest -k a\ b", False, ["pytest", "-k", "a b"]),
])
def test_split_command_on_each_platform(command, windows, expected):
    # quotes are removed everywhere; a backslash is a path separator on Windows and an escape elsewhere
    assert runner.split_command(command, windows=windows) == expected


@pytest.mark.parametrize("command, message", [
    ("", "Enter a test command"),
    ("X=1", "Enter a test command"),
    ("rm -rf /", "must run pytest"),
    ("python run_demo.py --brave", "not scripts like run_demo.py"),
    ("source .venv/bin/activate && python -m pytest demo", "single command"),
    ("cd demo; pytest", "single command"),
    ("pytest 'unclosed", "can't be read"),
])
def test_bad_commands(command, message):
    with pytest.raises(ValueError, match=message):
        runner.parse_command(command)


def test_shown_command_hides_secret_looking_values():
    env = {"DEMO_BROWSER": "brave", "API_TOKEN": "t0k3n", "db_password": "pw"}
    assert runner.shown_command(env, ["pytest", "-v"]) == "DEMO_BROWSER=brave API_TOKEN=*** db_password=*** pytest -v"


def test_test_settings_live_in_the_project(project, tmp_path):
    machine = replace(project, test_command="python -m pytest elsewhere")
    assert settings.apply(machine) == machine  # no settings file yet: this computer's settings apply
    saved = settings.save(machine, "python  -m pytest tests", str(tmp_path / "tests"), False, "Ada")
    file = tmp_path / ".gherkin-copilot" / "settings.json"
    assert json.loads(file.read_text()) == {"test_command": "python -m pytest tests", "run_from": "tests",
                                            "capture": False}  # relative, so it works on every computer
    assert (saved.test_command, saved.run_from, saved.capture) == ("python -m pytest tests", str(tmp_path / "tests"),
                                                                   False)
    assert settings.apply(machine) == saved  # the project's file wins
    entry = history.entries(project)[0]
    assert entry["action"] == "settings" and entry["author"] == "Ada" and entry["files"] == [
        ".gherkin-copilot/settings.json"]
    settings.save(machine, "python -m pytest tests", str(tmp_path / "tests"), False, "Ada")
    assert len(history.entries(project)) == 1  # nothing changed, nothing logged

    with pytest.raises(ValueError, match="API_TOKEN would be committed"):
        settings.save(machine, "API_TOKEN=x python -m pytest", "", True, "Ada")
    with pytest.raises(ValueError, match="doesn't exist"):
        settings.save(machine, "python -m pytest", str(tmp_path / "missing"), True, "Ada")
    file.write_text("{oops")
    with pytest.raises(ValueError, match="can't be read"):
        settings.apply(machine)
    assert settings.save(machine, "pytest", "", True, "Ada").test_command == "pytest"  # saving again replaces it


def test_names_and_keyword_expression():
    assert runner.python_name("Add two products") == "test_add_two_products"
    assert runner.python_name("1st: log in, then out!") == "test_st_log_in_then_out"
    collected = ["t.py::test_add", "t.py::test_add_copy", "t.py::test_out[a-b]"]
    assert runner.keyword_expression({"test_add"}, collected) == "(test_add) and not (test_add_copy)"
    assert runner.keyword_expression({"test_out"}, collected) == "test_out"
    with pytest.raises(LookupError):
        runner.keyword_expression({"test_missing"}, collected)


def test_run_all_then_one_scenario(project):
    work = runner.Runner()
    work.start(project, "all")
    with pytest.raises(ValueError, match="already running"):
        work.start(project, "all")
    done = wait(lambda: work.status("p"))
    assert done["status"] == "finished" and done["exit_code"] == 1 and done["done"] == 2
    results = runner.latest_results(project)
    assert results is not None
    scenarios = {s["name"]: s for f in results["features"] for s in f["scenarios"]}
    assert scenarios["Adding works"]["status"] == "passed"
    assert scenarios["Adding works again"]["status"] == "failed"
    assert "assert 3 == 4" in scenarios["Adding works again"]["steps"][1]["error"]
    assert results["run"]["label"] == "All tests"
    assert (runner.results_dir(project) / ".gitignore").read_text() == "*\n"

    work.start(project, "scenario", "sums.feature", "Adding works")
    done = wait(lambda: work.status("p"))
    assert done["exit_code"] == 0
    results = runner.latest_results(project)
    assert results is not None
    names = [s["name"] for f in results["features"] for s in f["scenarios"]]
    assert names == ["Adding works"]  # "Adding works again" also contains the name, but isn't run


def test_a_run_shows_as_ended_only_once_its_results_are_saved(project, monkeypatch):
    """The app loads the results as soon as a run ends; run.json used to be written just after (flaky on Windows)."""
    work = runner.Runner()
    shown_while_saving = []
    write_text = Path.write_text

    def spy(path, *args, **kwargs):
        if path.name == "run.json":
            shown_while_saving.append((work.status("p") or {}).get("status"))
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    work.start(project, "all")
    assert wait(lambda: work.status("p"))["status"] == "finished"
    assert shown_while_saving == ["running"]
    assert json.loads((runner.results_dir(project) / "run.json").read_text())["status"] == "finished"


def test_results_never_go_through_a_link_out_of_the_project(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "results").mkdir()
    (outside / "results" / "keep.txt").write_text("not the app's")
    try:
        os.symlink(outside, runner.results_dir(project).parent, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")
    work = runner.Runner()
    work.start(project, "all")
    done = wait(lambda: work.status("p"))
    assert done["status"] == "error" and "outside the project" in done["log"][-1]
    assert (outside / "results" / "keep.txt").exists()  # used to be deleted with the old results


def test_scopes_that_cant_run(project):
    work = runner.Runner()
    work.start(project, "file", "other.feature")
    done = wait(lambda: work.status("p"))
    assert done["status"] == "error" and "Is the feature file loaded by a test module" in done["log"][-1]
    with pytest.raises(ValueError, match="Save your changes first"):
        work.start(project, "scenario", "sums.feature", "No such scenario")
    with pytest.raises(ValueError):
        work.start(project, "file", "../tests/test_sums.py")
    with pytest.raises(ValueError, match="Set this project's test command"):
        work.start(Project("q", "Q", project.steps, project.features), "all")
    with pytest.raises(ValueError, match="Nothing is running"):
        work.stop("p")


def test_stop_while_collecting_never_starts_the_run(project):
    (Path(project.steps) / "tests" / "conftest.py").write_text("import time\n\ntime.sleep(3)  # slow collection\n")
    work = runner.Runner()
    work.start(project, "scenario", "sums.feature", "Adding works")
    time.sleep(0.5)
    work.stop("p")
    done = wait(lambda: work.status("p"))
    assert done["status"] == "stopped" and not any(line.startswith("$ cd") for line in done["log"])


def test_stop(project):
    (Path(project.steps) / "tests" / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")
    work = runner.Runner()
    work.start(project, "all")
    time.sleep(1.5)
    work.stop("p")
    assert wait(lambda: work.status("p"))["status"] == "stopped"


def test_settings_and_artifacts_over_http(project, tmp_path):
    store = ProjectStore(tmp_path / "projects.json")
    added = store.add("P", project.steps, project.features)
    server, app, _ = start_in_background(store)
    port = server.server_address[1]
    try:
        body = {"id": added.id, "test_command": "bash -c 'echo hi'", "run_from": ""}
        status, _, data, _ = request(port, "POST", "/api/projects/tests", body, app.token)
        assert status == 400 and "must run pytest" in json.loads(data)["error"]
        body["test_command"] = "python -m pytest tests"
        assert request(port, "POST", "/api/projects/tests", body)[0] == 403  # needs the token
        data = json.loads(request(port, "POST", "/api/projects/tests", {**body, "author": "Ada"}, app.token)[2])
        assert data["projects"][0]["test_command"] == "python -m pytest tests"
        assert (Path(project.steps) / ".gherkin-copilot" / "settings.json").is_file()
        assert [p.test_command for p in store.load()[0]] == [""]  # in the project, not in this computer's list
        status, _, data, _ = request(port, "POST", "/api/projects/tests",
                                     {**body, "test_command": "API_KEY=x pytest"}, app.token)
        assert status == 400 and "API_KEY would be committed" in json.loads(data)["error"]

        shots = runner.results_dir(project) / "artifacts" / "test-x"
        shots.mkdir(parents=True)
        (shots / "shot.png").write_bytes(b"\x89PNG")
        (runner.results_dir(project).parent / "history.jsonl").write_text("{}\n")
        def get(path):
            return request(port, "GET", f"/api/artifact?project={added.id}&path={path}")
        assert get("artifacts/test-x/shot.png")[:2] == (200, "image/png")
        assert get("../history.jsonl")[0] == 400
        assert get("run.json")[0] == 400

        record = {"project": added.id, "started": 1.0, "title": "Smoke", "author": "Ada"}
        status, _, data, _ = request(port, "POST", "/api/record", record, app.token)
        assert status == 400 and "changed since" in json.loads(data)["error"]

        state = json.loads(request(port, "GET", f"/api/run?project={added.id}")[2])
        assert state["run"] is None and state["default_folder"] == project.steps
    finally:
        server.shutdown()
        server.server_close()


def test_steps_and_features_on_different_windows_drives(project, monkeypatch):
    def different_drives(paths):
        raise ValueError("Paths don't have the same drive")  # what os.path.commonpath says on Windows for C: and D:

    monkeypatch.setattr(os.path, "commonpath", different_drives)
    features = Path(project.features)
    assert runner.default_folder(project) == features and history.project_root(project) == features
    history.record(project, "edit", "Ada", {"sums.feature": ("a\n", "b\n")})  # recording a save no longer fails
    assert history.entries(project)[0]["author"] == "Ada"


def test_default_folder_and_not_found_hint(project, tmp_path):
    assert runner.default_folder(project) == Path(project.steps)
    split = Project("s", "S", str(tmp_path / "a" / "steps"), str(tmp_path / "a" / "features"))
    assert runner.default_folder(split) == tmp_path / "a"

    wrong = Project("p", "P", project.steps, project.features, "python -m pytest nowhere", "", False)
    work = runner.Runner()
    work.start(wrong, "all")
    done = wait(lambda: work.status("p"))
    assert f"$ cd {project.steps}" in done["log"]
    assert "couldn't find “nowhere”" in done["log"][-1]


def finished_run(project):
    work = runner.Runner()
    work.start(project, "all")
    run = wait(lambda: work.status("p"))
    for name in ("test-adding-works", "test-adding-works-again"):  # as if screenshots were on
        shots = runner.results_dir(project) / "artifacts" / f"tests-test-sums-py-{name}"
        shots.mkdir(parents=True)
        (shots / "test-finished-1.png").write_bytes(b"\x89PNG")
        (shots / "trace.zip").write_bytes(b"PK")
    return run


def test_save_a_run_as_a_test_record(project):
    run = finished_run(project)
    root = Path(project.steps)
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "refs" / "heads" / "main").write_text("c9b6fb9" + "0" * 33 + "\n")

    saved = records.save(project, run["started"], " Release 2.3  regression ", "Ada", "Windows 11, Edge",
                         "First <b>try</b>")
    day = time.strftime("%Y-%m-%d", time.localtime(run["started"]))
    assert saved["folder"] == f"test-records/{day}_release-2-3-regression"
    folder = root / saved["folder"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "cucumber.json", "record.json", "report.html",
        "screenshots/tests-test-sums-py-test-adding-works-again/test-finished-1.png",  # failed only, no traces
    ]
    assert (folder / "cucumber.json").read_bytes() == (runner.results_dir(project) / "cucumber.json").read_bytes()
    record = json.loads((folder / "record.json").read_text())
    assert record["title"] == "Release 2.3 regression" and record["result"] == "failed"
    assert record["pytest_exit_code"] == 1 and record["versions"][0].startswith("platform ")
    assert "pytest-" in record["versions"][0] and record["versions"][1].startswith("plugins: ")
    assert "bdd-" in record["versions"][1]  # the order of plugins depends on what's installed
    assert record["scenarios"]["total"] == 2 and record["scenarios"]["passed"] == 1
    assert record["ran_by"] == "Ada" and record["what_ran"] == "All tests" and record["environment"] == "Windows 11, Edge"
    assert record["command"].startswith(f"{sys.executable} -m pytest tests -p no:cacheprovider --cucumberjson=")
    assert record["git"] == {"branch": "main", "commit": "c9b6fb9" + "0" * 33}
    html = (folder / "report.html").read_text()
    assert "<h1>Release 2.3 regression</h1>" in html and "<dd>1 of 2 failed</dd>" in html
    assert "<dd>main @ c9b6fb9</dd>" in html and "First &lt;b&gt;try&lt;/b&gt;" in html
    assert 'src="screenshots/tests-test-sums-py-test-adding-works-again/test-finished-1.png"' in html

    again = records.save(project, run["started"], "Release 2.3 regression", "Ada")
    assert again["folder"].endswith("_release-2-3-regression-2")  # never overwrites a record
    assert records.save(project, run["started"], "日本", "Ada")["folder"].endswith("_record")


def test_a_record_is_only_a_pass_if_pytest_agrees(project):
    work = runner.Runner()
    work.start(project, "scenario", "sums.feature", "Adding works")
    run = wait(lambda: work.status("p"))
    assert records.save(project, run["started"], "Smoke", "Ada")["record"]["result"] == "passed"
    run_file = runner.results_dir(project) / "run.json"
    for exit_code in (1, None):  # e.g. a test outside the feature files failed, or no exit code was saved
        run_file.write_text(json.dumps({**json.loads(run_file.read_text()), "exit_code": exit_code}))
        saved = records.save(project, run["started"], "Smoke", "Ada")
        assert saved["record"]["result"] == "mismatch" and saved["record"]["pytest_exit_code"] == exit_code
        html = (Path(project.steps) / saved["folder"] / "report.html").read_text()
        assert f"match pytest: the results say 1 passed, pytest&#x27;s exit code was {exit_code}</dd>" in html


def test_runs_that_cant_be_saved_as_records(project, tmp_path_factory, monkeypatch):
    run = finished_run(project)
    started = run["started"]
    with pytest.raises(ValueError, match="title"):
        records.save(project, started, "  ", "Ada")
    with pytest.raises(ValueError, match="your name"):
        records.save(project, started, "Smoke", "")
    with pytest.raises(ValueError, match="changed since"):
        records.save(project, started - 1, "Smoke", "Ada")  # the page showed an older run
    run_file = runner.results_dir(project) / "run.json"
    run_file.write_text(json.dumps({**json.loads(run_file.read_text()), "status": "stopped"}))
    with pytest.raises(ValueError, match="Only a run that finished"):
        records.save(project, started, "Smoke", "Ada")
    run_file.write_text(json.dumps({**json.loads(run_file.read_text()), "status": "finished"}))

    def full_disk(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(records, "render_html", full_disk)
    with pytest.raises(OSError):
        records.save(project, started, "Smoke", "Ada")
    assert not any(records.records_dir(project).iterdir())  # no half-saved record left behind
    records.records_dir(project).rmdir()

    outside = tmp_path_factory.mktemp("outside")
    try:
        os.symlink(outside, records.records_dir(project), target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")
    with pytest.raises(ValueError, match="outside the project"):
        records.save(project, started, "Smoke", "Ada")
    assert not any(outside.iterdir())
