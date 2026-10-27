"""Test records: a test run the team keeps.

Every run in the app is informal: its results go to ``.gherkin-copilot/results``, which git ignores, and the
next run replaces them. Saving a record copies the run on screen into ``test-records/<date>_<title>/`` in the
project, to be committed: the raw results, a report page anyone can open, and who ran it, where and on which
commit. Only screenshots of scenarios that didn't pass are copied; traces and videos aren't, as they can hold
whatever a test typed, passwords included. The app never changes or deletes a record.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from gherkin_copilot import __version__
from gherkin_copilot.files import resolves_inside
from gherkin_copilot.report import STATUS_ORDER, attach_artifacts, load_report, render_html
from gherkin_copilot.ui import gitinfo
from gherkin_copilot.ui.builder import json_object
from gherkin_copilot.ui.history import clean_author, project_root
from gherkin_copilot.ui.projects import Project
from gherkin_copilot.ui.runner import results_dir

FOLDER = "test-records"
MAX_TITLE = 80
MAX_ENVIRONMENT = 120
MAX_NOTES = 1000


def records_dir(project: Project) -> Path:
    return project_root(project) / FOLDER


def _one_line(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _local(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds).astimezone()


def _git(root: Path) -> dict[str, str | None] | None:
    """Branch and commit, read from the refs only (no history walk, never runs git)."""
    found = gitinfo.find_repository(root)
    if found is None:
        return None
    git_dir, common = found
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else None
    return {"branch": branch, "commit": gitinfo.resolve(git_dir, common, "HEAD")}


def save(project: Project, started: object, title: object, author: object, environment: object = "",
         notes: object = "") -> dict[str, Any]:
    """Copy the latest run into a new record folder. ``started`` is the run the user is looking at."""
    title_text, ran_by = _one_line(title, MAX_TITLE), clean_author(author)
    if not title_text:
        raise ValueError("Give the record a title, for example “Release 2.3 regression”.")
    if not ran_by:
        raise ValueError("Enter your name: a record says who ran the tests.")
    results = results_dir(project)
    run_file, report_file = results / "run.json", results / "cucumber.json"
    run = (json_object(json.loads(run_file.read_text(encoding="utf-8"))) if run_file.is_file() else None) or {}
    if not isinstance(started, int | float) or run.get("started") != started:
        raise ValueError("These results have changed since the page showed them. Look at the latest run, then save.")
    if run.get("status") != "finished" or not report_file.is_file():
        raise ValueError("Only a run that finished can be saved as a record.")
    try:
        features = load_report(report_file)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("The results file couldn't be read.") from exc
    scenarios = [s for f in features for s in f.scenarios]
    if not scenarios:
        raise ValueError("This run has no scenario results to save.")

    root, base = project_root(project), records_dir(project)
    if not resolves_inside(base, root):
        raise ValueError(f"{base} links to somewhere outside the project, so the app won't write to it.")
    base.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title_text.lower()).strip("-")[:50].strip("-") or "record"
    name = f"{_local(started):%Y-%m-%d}_{slug}"
    folder, n = base / name, 1
    while folder.exists():
        n += 1
        folder = base / f"{name}-{n}"
    folder.mkdir()
    try:
        shutil.copyfile(report_file, folder / "cucumber.json")
        attach_artifacts(features, results / "artifacts")
        for s in scenarios:
            failed = s.status not in {"passed", "skipped"}
            copies: list[Path] = []
            for shot in s.artifacts.get("screenshots", []) if failed else []:
                if resolves_inside(shot, results):
                    copy = folder / "screenshots" / shot.parent.name / shot.name
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(shot, copy)
                    copies.append(copy)
            s.artifacts = {"screenshots": copies} if copies else {}

        counts = {status: sum(s.status == status for s in scenarios) for status in STATUS_ORDER}
        failed_count = len(scenarios) - counts["passed"] - counts["skipped"]
        summary = f"{failed_count} of {len(scenarios)} failed" if failed_count else f"{counts['passed']} passed"
        # A false pass is the one mistake a record mustn't make: it's only a pass if pytest's exit code agrees
        exit_code = run.get("exit_code")
        result = "failed" if failed_count else "passed"
        if (exit_code == 0) != (result == "passed"):
            result = "mismatch"
            summary = f"Verdict doesn't match pytest: the results say {summary}, pytest's exit code was {exit_code}"
        ended = run.get("ended")
        git = _git(root)
        record: dict[str, Any] = {
            "title": title_text,
            "result": result,
            "pytest_exit_code": exit_code,
            "scenarios": {"total": len(scenarios), **counts},
            "what_ran": run.get("label"),
            "command": run.get("command"),
            "started": _local(started).isoformat(timespec="seconds"),
            "ended": _local(ended).isoformat(timespec="seconds") if isinstance(ended, int | float) else None,
            "ran_by": ran_by,
            "environment": _one_line(environment, MAX_ENVIRONMENT),
            "notes": notes.strip()[:MAX_NOTES] if isinstance(notes, str) else "",
            "computer": platform.platform(),
            "versions": run.get("versions") or [],  # what pytest says was installed, not what the lock file asks for
            "git": git,
            "saved": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": f"gherkin-copilot {__version__}",
        }
        commit = f"{git['branch'] or 'detached'} @ {(git['commit'] or '')[:7]}" if git and git["commit"] else ""
        details = [
            ("Result", summary),
            ("What ran", str(run.get("label") or "")),
            ("Command", str(run.get("command") or "")),
            ("Ran on", f"{_local(started):%Y-%m-%d %H:%M}"),
            ("Ran by", ran_by),
            ("Environment", record["environment"]),
            ("Computer", record["computer"]),
            ("Versions", "; ".join(record["versions"])),
            ("Commit", commit),
            ("Notes", record["notes"]),
        ]
        (folder / "report.html").write_text(render_html(features, folder, "cucumber.json", title_text, details),
                                            encoding="utf-8")
        (folder / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # only the folder made above: no half-saved record
        raise
    return {"folder": folder.relative_to(root).as_posix(), "record": record}
