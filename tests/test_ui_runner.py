import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_ui import request

from gherkin_copilot.ui import history, runner
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
        data = json.loads(request(port, "POST", "/api/projects/tests", body, app.token)[2])
        assert data["projects"][0]["test_command"] == "python -m pytest tests"

        shots = runner.results_dir(project) / "artifacts" / "test-x"
        shots.mkdir(parents=True)
        (shots / "shot.png").write_bytes(b"\x89PNG")
        (runner.results_dir(project).parent / "history.jsonl").write_text("{}\n")
        def get(path):
            return request(port, "GET", f"/api/artifact?project={added.id}&path={path}")
        assert get("artifacts/test-x/shot.png")[:2] == (200, "image/png")
        assert get("../history.jsonl")[0] == 400
        assert get("run.json")[0] == 400

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
