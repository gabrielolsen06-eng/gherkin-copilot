"""Regression tests for the 2026-10-11 review: missing paths, bad files, Windows consoles."""

import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from gherkin_copilot.cli import main
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
    assert "  OK   5  Given I am on the login page" in text and "OK Given I am on the login page" in text


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
