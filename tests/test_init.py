"""`gherkin-copilot init`: the starter project it writes, and that it never overwrites anything."""

import json
import re
import subprocess  # nosec B404: runs pytest on the generated project, fixed argument list
import sys
from pathlib import Path

import pytest

from gherkin_copilot.cli import main
from gherkin_copilot.scaffold import templates

REPO = Path(__file__).parent.parent
EXPECTED = [
    ".gitignore", "README.md", "app_server.py", "conftest.py", "features/example.feature", "pytest.ini", "requirements.in",
    "requirements.lock", "steps/__init__.py", "steps/example_steps.py", "steps/navigation_steps.py",
    "tests/test_features.py",
]


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def tag_every_scenario(project: Path) -> None:
    """Testers add tags in the app, and pytest-bdd turns each into a pytest mark: collection must still work."""
    for feature in (project / "features").glob("*.feature"):
        text = feature.read_text(encoding="utf-8")
        feature.write_text(re.sub(r"^( *)(Scenario)", r"\1@smoke @check-out\n\1\2", text, flags=re.M), encoding="utf-8")


def test_creates_the_starter_project_in_a_new_folder(tmp_path, capsys):
    root = tmp_path / "new" / "payments-tests"
    code, out, _ = run(["init", str(root), "--name", "Payments\n tests"], capsys)
    assert code == 0 and "Created 12 files" in out and "python -m pytest --browser-channel msedge" in out
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == EXPECTED
    assert (root / "README.md").read_text(encoding="utf-8").startswith("# Payments tests\n")


def test_never_overwrites_existing_files(tmp_path, capsys):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmine = yes\n")
    code, out, _ = run(["init", str(tmp_path)], capsys)
    assert code == 0 and "Created 11 files" in out and "Kept 1 file" in out and "  = pytest.ini" in out
    assert (tmp_path / "pytest.ini").read_text() == "[pytest]\nmine = yes\n"
    assert f"# {tmp_path.name}\n" in (tmp_path / "README.md").read_text(encoding="utf-8")  # name from the folder

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    code, out, _ = run(["init", str(tmp_path)], capsys)
    assert code == 0 and out.startswith("Nothing to create")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_dry_run_and_json_write_nothing(tmp_path, capsys):
    root = tmp_path / "plan"
    code, out, _ = run(["init", str(root), "--dry-run"], capsys)
    assert code == 0 and "Would create 12 files" in out and "Nothing was written" in out and not root.exists()

    code, out, _ = run(["init", str(root), "--dry-run", "--json"], capsys)
    data = json.loads(out)
    assert code == 0 and data["dry_run"] and data["created"] == EXPECTED and data["skipped"] == []
    assert data["root"] == root.as_posix() and not root.exists()


def test_bad_targets_exit_2(tmp_path, capsys):
    target = tmp_path / "a-file.txt"
    target.write_text("x")
    code, _, err = run(["init", str(target)], capsys)
    assert code == 2 and "Not a folder" in err and target.read_text() == "x"
    code, _, err = run(["init", str(tmp_path / "x"), "--name", "  "], capsys)
    assert code == 2 and "--name" in err


def test_starter_dependencies_match_the_repo(tmp_path):
    files = templates()
    # same hash-checked lock as this repo's own (CI audits that one with pip-audit)
    assert files["requirements.lock"] == (REPO / "requirements.lock").read_text(encoding="utf-8")

    def pins(text):
        return sorted(line for line in text.splitlines() if line and not line.startswith("#"))

    assert pins(files["requirements.in"]) == pins((REPO / "requirements.in").read_text(encoding="utf-8"))


def test_generated_project_binds_every_step_and_collects(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    capsys.readouterr()
    code, out, _ = run(["check", str(tmp_path / "features"), "--path", str(tmp_path)], capsys)
    assert code == 0 and "2 ok, 0 missing, 0 ambiguous" in out
    for path in tmp_path.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    # pytest itself loads conftest.py, every step file and pytest.ini (running it needs a browser: CI does that)
    # --cucumberjson into a folder that doesn't exist yet, as every CI run on a fresh checkout does
    tag_every_scenario(tmp_path)
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-W", "error",  # nosec B603
                           "--cucumberjson=reports/cucumber.json"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "tests/test_features.py::test_the_browser_shows_a_page" in done.stdout
    assert (tmp_path / "reports" / "cucumber.json").is_file()


def test_gitlab_pipeline_only_when_asked(tmp_path, capsys):
    assert main(["init", str(tmp_path / "plain")]) == 0
    assert not (tmp_path / "plain" / ".gitlab-ci.yml").exists()
    code, out, _ = run(["init", str(tmp_path / "gl"), "--ci", "gitlab"], capsys)
    pipeline = (tmp_path / "gl" / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert code == 0 and "Created 13 files" in out and "  + .gitlab-ci.yml" in out
    assert "--require-hashes -r requirements.lock" in pipeline and "junit: reports/junit.xml" in pipeline
    assert "    access: developer\n" in pipeline  # traces hold typed passwords and cookies
    with pytest.raises(SystemExit) as unknown:  # argparse rejects a CI system it doesn't know
        main(["init", str(tmp_path / "x"), "--ci", "jenkins"])
    assert unknown.value.code == 2 and not (tmp_path / "x").exists()


def test_gitlab_image_matches_the_locked_playwright():
    # Playwright's image only holds the browsers for its own version: a mismatch means a download in CI
    lock = (REPO / "requirements.lock").read_text(encoding="utf-8")
    version = next(line.split("==")[1].split()[0] for line in lock.splitlines() if line.startswith("playwright=="))
    image = f"mcr.microsoft.com/playwright/python:v{version}-noble"
    assert image in templates("gitlab")[".gitlab-ci.yml"]
    assert image in (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")  # CI tests the same image


# --- init --desktop ---------------------------------------------------------------------------

DESKTOP_DEMO = REPO / "desktop_demo"


def test_desktop_starter_is_the_repos_desktop_demo():
    """The starter's desktop files are copies of desktop_demo/, which CI runs on Windows; copy any change across."""
    made = templates(desktop=True)
    copies = [p for p in made if p not in ("README.md", "requirements-desktop.lock") and p not in
              {".gitignore", "requirements.in", "requirements.lock"}]
    assert sorted(copies) == ["conftest.py", "features/character_map.feature", "pytest.ini", "steps/__init__.py",
                              "steps/app_steps.py", "tests/test_features.py", "uia.py"]
    for path in copies:
        assert made[path] == (DESKTOP_DEMO / path).read_text(encoding="utf-8"), f"{path} differs from desktop_demo/{path}"
    assert made["requirements-desktop.lock"] == (REPO / "requirements-desktop.lock").read_text(encoding="utf-8")
    assert made["requirements.lock"] == (REPO / "requirements.lock").read_text(encoding="utf-8")
    assert "example.feature" not in " ".join(made) and "navigation_steps.py" not in " ".join(made)  # no web files


def test_desktop_project_binds_every_step_and_skips_off_windows(tmp_path, capsys):
    code, out, _ = run(["init", str(tmp_path), "--desktop", "--name", "Billing tests"], capsys)
    assert code == 0 and "Created 12 files" in out and "requirements-desktop.lock" in out
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# Billing tests\n")
    code, out, _ = run(["check", str(tmp_path / "features"), "--path", str(tmp_path / "steps")], capsys)
    assert code == 0 and "10 ok, 0 missing, 0 ambiguous" in out
    # pytest loads conftest.py (with its pytest.ini settings), the steps and uia.py, and finds every scenario
    def pytest(*args):
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "error", *args],  # nosec B603
                              cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)

    tag_every_scenario(tmp_path)
    collected = pytest("--collect-only")
    assert collected.returncode == 0 and "3 tests collected" in collected.stdout, collected.stdout + collected.stderr
    if sys.platform != "win32":  # running them needs Windows (and comtypes): CI's desktop job does that
        done = pytest("-rs")
        assert done.returncode == 0 and "needs Windows" in done.stdout, done.stdout + done.stderr


def test_desktop_init_refuses_a_linux_pipeline(tmp_path, capsys):
    code, _, err = run(["init", str(tmp_path / "d"), "--desktop", "--ci", "gitlab"], capsys)
    assert code == 2 and "Windows runner" in err and not (tmp_path / "d").exists()
