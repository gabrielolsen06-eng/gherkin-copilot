"""Regression tests for the 2026-10-11 review and its 2026-10-12 re-review: missing paths, bad files,
Windows consoles, literal plain-string steps."""

import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from gherkin_copilot import cli
from gherkin_copilot.cli import main
from gherkin_copilot.indexer import build_index
from gherkin_copilot.matcher import find, is_exact_match
from gherkin_copilot.report import load_report

DEMO = Path(__file__).parent.parent / "demo"


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# H1: a missing or empty features path must fail, or a CI gate turns green on a typo
@pytest.mark.parametrize("argv", [
    ["check", "demo/featurez", "--path", str(DEMO)],
    ["find", "Given x", "--path", "no/such/folder"],
    ["dupes", "--path", "no/such/folder"],
    ["index", "no/such/folder", "--no-save"],
    ["merge", "no/such/folder"],
])
def test_missing_paths_exit_2(argv, capsys):
    code, _, err = run(argv, capsys)
    assert code == 2 and "not found" in err and "Traceback" not in err


def test_check_with_no_feature_files_exits_2(tmp_path, capsys):
    code, _, err = run(["check", str(tmp_path), "--path", str(DEMO)], capsys)
    assert code == 2 and "No .feature files found" in err


# H3: ✓ and ✗ can't be written to a Windows code page (log files, pipes)
def test_windows_console_falls_back_to_words(monkeypatch):
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="cp1252"))
    assert main(["check", str(DEMO / "features"), "--path", str(DEMO)]) == 0
    assert main(["find", "Given I am on the login page", "--path", str(DEMO)]) == 0
    sys.stdout.flush()
    text = buffer.getvalue().decode("cp1252")
    assert "  OK  12  Given I am on the login page" in text and "OK Given I am on the login page" in text


# H4: Cucumber-JVM / Cucumber-JS report a Background as its own element before each scenario
def test_failed_background_steps_stay_with_their_scenario(tmp_path):
    report = tmp_path / "cucumber.json"
    passed = {"status": "passed", "duration": 1_000_000}
    report.write_text(json.dumps([{"name": "Cart", "uri": "cart.feature", "elements": [
        {"type": "background", "name": "", "steps": [
            {"keyword": "Given ", "name": "I am logged in", "line": 4,
             "result": {"status": "failed", "error_message": "login page did not load"}}]},
        {"type": "scenario", "name": "Add a product", "id": "cart;add", "line": "7", "steps": [
            {"keyword": "When ", "name": "I add a product", "line": 8, "result": {"status": "skipped"}}]},
        {"type": "background", "name": "", "steps": [
            {"keyword": "Given ", "name": "I am logged in", "line": 4, "result": passed}]},
        {"type": "scenario", "name": "Empty cart", "id": "cart;empty", "line": 11, "steps": [
            {"keyword": "Then ", "name": "the cart is empty", "line": 12, "result": passed}]},
    ]}]))
    first, second = load_report(report)[0].scenarios
    assert first.status == "failed" and first.steps[0].error == "login page did not load"
    assert [s.name for s in first.steps] == ["I am logged in", "I add a product"] and first.line == 7
    assert second.status == "passed" and len(second.steps) == 2


# H5: bad input files give one readable line and exit 2, never a traceback
def test_malformed_index_report_and_feature_files(tmp_path, capsys):
    index = tmp_path / "index.json"
    index.write_text("not json")
    code, _, err = run(["find", "Given x", "--index", str(index)], capsys)
    assert code == 2 and "isn't a valid index file" in err and err.count("\n") == 1

    report = tmp_path / "cucumber.json"
    report.write_text('[{"elements": "oops"}]')
    code, _, err = run(["report", str(report), "-o", str(tmp_path / "r.html")], capsys)
    assert code == 2 and "isn't a valid Cucumber JSON report" in err

    feature = tmp_path / "latin1.feature"
    feature.write_bytes("Feature: caf\xe9\n".encode("latin-1"))
    code, _, err = run(["check", str(feature), "--path", str(DEMO)], capsys)
    assert code == 2 and "isn't UTF-8 text" in err


# M2: a saved index that is older than the step files says so
def test_stale_index_warns(tmp_path, capsys, monkeypatch):
    project = tmp_path / "project"
    shutil.copytree(DEMO / "steps", project / "steps")
    monkeypatch.chdir(tmp_path)
    assert main(["index", str(project)]) == 0
    capsys.readouterr()
    assert main(["find", "Given I am on the login page"]) == 0
    assert "changed after the saved index" not in capsys.readouterr().err

    later = time.time() + 5
    os.utime(project / "steps" / "cart_steps.py", (later, later))
    main(["find", "Given I am on the login page"])
    assert "cart_steps.py changed after the saved index was made" in capsys.readouterr().err


# N1: pytest-bdd matches a plain string literally, braces and all; behave reads {name} as a placeholder
def test_plain_string_steps_match_literally(tmp_path):
    (tmp_path / "bdd_steps.py").write_text(
        "from pytest_bdd import given\n\n"
        "@given(\"I have {x} apples\")\ndef apples():\n    pass\n\n"
        "@given(\"the json is {{}}\")\ndef empty_json():\n    pass\n")
    (tmp_path / "behave_steps.py").write_text(
        "from behave import given\n\n@given(\"I own {n} pears\")\ndef pears(context, n):\n    pass\n")
    steps = {s.function: s for s in build_index(tmp_path)}
    assert is_exact_match("Given I have {x} apples", steps["apples"])
    assert not is_exact_match("Given I have 5 apples", steps["apples"])
    assert is_exact_match("Given the json is {{}}", steps["empty_json"])
    assert not is_exact_match("Given the json is {}", steps["empty_json"])
    assert is_exact_match("Given I own 3 pears", steps["pears"])
    best = find("Given the json is {{}}", list(steps.values()))[0]
    assert best.exact and best.step.function == "empty_json"


# N2: a line number that is infinite, NaN or too big for a float is junk, not a crash
@pytest.mark.parametrize("line", ["1e400", "Infinity", "-Infinity", "NaN", "\"1e400\"", "1" + "0" * 400])
def test_report_ignores_line_numbers_that_are_not_finite(tmp_path, capsys, line):
    report = tmp_path / "cucumber.json"
    step = '{"keyword": "Given ", "name": "x", "line": ' + line + ', "result": {"status": "passed"}}'
    report.write_text('[{"name": "F", "elements": [{"type": "scenario", "name": "S", "line": ' + line
                      + ', "steps": [' + step + ']}]}]')
    scenario = load_report(report)[0].scenarios[0]
    assert scenario.line == 0 and scenario.steps[0].line == 0
    code, _, err = run(["report", str(report), "-o", str(tmp_path / "r.html")], capsys)
    assert code == 0 and err == ""


# N3: a wrong value inside an otherwise valid index gets the same friendly message as bad JSON
def test_index_with_a_bad_created_time(tmp_path, capsys):
    index = tmp_path / "index.json"
    index.write_text('{"root": ".", "created": "abc", "steps": []}')
    code, _, err = run(["find", "Given x", "--index", str(index)], capsys)
    assert code == 2 and "isn't a valid index file" in err and "could not convert" not in err


# N4: only the loaders' errors count as bad input; a bug elsewhere is an internal error with details
@pytest.mark.parametrize("error", [KeyError("step"), TypeError("bug"), ValueError("bug")])
def test_bugs_are_internal_errors_not_usage_errors(capsys, monkeypatch, error):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "find", broken)
    code, _, err = run(["find", "Given I am on the login page", "--path", str(DEMO)], capsys)
    assert code == 3 and "Traceback" in err and "internal error" in err


# N5: messages count properly and show forward-slash paths
def test_index_message_and_paths(tmp_path, capsys):
    (tmp_path / "one.py").write_text("from pytest_bdd import given\n\n@given(\"x\")\ndef x():\n    pass\n")
    index = tmp_path / "sub" / "index.json"
    index.parent.mkdir()
    assert main(["index", str(tmp_path), "--index", str(index)]) == 0
    assert f"1 step definition indexed, saved to {index.as_posix()}" in capsys.readouterr().err
