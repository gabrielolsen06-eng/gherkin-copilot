"""A record of every change the app makes to a project.

Kept inside the project, in ``.gherkin-copilot/history.jsonl`` (one JSON object
per line, append-only), so it can be committed and shared with the team. Each
entry stores what changed as a diff, and for scenario changes the scenario's
text before and after, so it can be restored later.
"""

from __future__ import annotations

import difflib
import json
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gherkin_copilot.ui.builder import json_object
from gherkin_copilot.ui.projects import Project

MAX_AUTHOR = 60
_lock = threading.Lock()


def project_root(project: Project) -> Path:
    """The folder that holds both steps and features, or the features folder if they're unrelated."""
    steps, features = Path(project.steps), Path(project.features)
    try:
        common = Path(os.path.commonpath([steps, features]))
    except ValueError:  # Windows: on different drives
        return features
    return common if common in (steps, features) else features


def history_file(project: Project) -> Path:
    return project_root(project) / ".gherkin-copilot" / "history.jsonl"


def clean_author(author: object) -> str:
    if not isinstance(author, str):
        return ""
    return " ".join(author.split())[:MAX_AUTHOR]


def record(project: Project, action: str, author: object, files: dict[str, tuple[str, str]],
           scenario: str = "", file: str = "", before: str | None = None, after: str | None = None) -> dict[str, Any]:
    """Append one entry. ``files`` maps shown path -> (before, after) file contents."""
    diff: list[str] = []
    for shown, (old, new) in sorted(files.items()):
        diff += difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{shown}", f"b/{shown}", lineterm="")
    entry = {
        "id": secrets.token_hex(6),
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "author": clean_author(author),
        "action": action,
        "file": file or next(iter(sorted(files)), ""),
        "files": sorted(files),
        "scenario": scenario,
        "diff": diff,
        "before": before,  # scenario text, when the change was to one scenario
        "after": after,
    }
    path = history_file(project)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    return entry


def entries(project: Project, limit: int = 300) -> list[dict[str, Any]]:
    """Newest first. Lines that aren't valid JSON (e.g. a bad merge of the file) are skipped."""
    path = history_file(project)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json_object(json.loads(line))
        except json.JSONDecodeError:
            continue
        if item is not None and isinstance(item.get("id"), str):
            out.append(item)
    return out[::-1][:limit]


def find(project: Project, entry_id: str) -> dict[str, Any]:
    entry = next((e for e in entries(project, limit=10**9) if e["id"] == entry_id), None)
    if entry is None:
        raise ValueError("That history entry doesn't exist.")
    return entry
