"""The home page: one project at a glance.

Counts come from the same scan the other screens use (step definitions via
``ast``, feature files as text), the app's history file and the git folder.
Duplicates and the last test run are already loaded by the page, so they
aren't repeated here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from gherkin_copilot import gherkin
from gherkin_copilot.checker import FeatureStep, binds, iter_feature_files, parse_feature
from gherkin_copilot.indexer import StepDef, build_index
from gherkin_copilot.ui import gitinfo, history
from gherkin_copilot.ui.catalog import step_info
from gherkin_copilot.ui.projects import Project

TOP = 5
UNUSED = 8
TAGS = 12
RECENT = 5


def statistics(project: Project) -> dict[str, Any]:
    steps_folder, features_folder = Path(project.steps), Path(project.features)
    index = build_index(steps_folder)
    lines: list[FeatureStep] = []
    tags: Counter[str] = Counter()
    files = scenarios = outlines = example_rows = 0
    for path in iter_feature_files(features_folder):
        shown = path.relative_to(features_folder).as_posix()
        lines += parse_feature(path, shown)
        feature = gherkin.parse(path.read_text(encoding="utf-8"))
        files += 1
        for block in feature.blocks:
            if block.kind == "Background":
                continue
            scenarios += 1
            outlines += block.outline
            example_rows += sum(len(e.rows) for e in block.examples)
            tags.update(set(feature.tags) | set(block.tags))  # scenarios inherit their feature's tags
    uses: Counter[StepDef] = Counter()
    missing = ambiguous = 0
    for line in lines:
        bound = [d for d in index if binds(line, d)]
        uses.update(bound)
        missing += not bound
        ambiguous += len(bound) > 1
    unused = [d for d in index if not uses[d]]
    return {
        "feature_files": files,
        "scenarios": scenarios,
        "outlines": outlines,
        "example_rows": example_rows,
        "feature_lines": len(lines),
        "definitions": len(index),
        "used": len(index) - len(unused),
        "missing": missing,
        "ambiguous": ambiguous,
        "top": [{**step_info(steps_folder, d), "uses": n} for d, n in uses.most_common(TOP)],
        "unused": [{**step_info(steps_folder, d), "uses": 0} for d in unused[:UNUSED]],
        "unused_count": len(unused),
        "tags": [{"tag": t, "scenarios": n} for t, n in tags.most_common(TAGS)],
    }


def recent_changes(project: Project) -> dict[str, Any]:
    entries = history.entries(project, limit=10**9)
    return {
        "changes": len(entries),
        "authors": len({e.get("author") for e in entries if e.get("author")}),
        "recent": [{k: e.get(k) for k in ("action", "file", "files", "scenario", "author", "time")}
                   for e in entries[:RECENT]],
    }


def overview(project: Project) -> dict[str, Any]:
    try:
        git = gitinfo.summary(history.project_root(project))
    except (OSError, ValueError):  # an unreadable .git folder shouldn't hide the rest of the page
        git = None
    return {"stats": statistics(project), "history": recent_changes(project), "git": git}
