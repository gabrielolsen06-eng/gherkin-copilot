"""Duplicates screen: list duplicate pairs and merge one pair at a time.

Uses the same planner as `gherkin-copilot merge`, so the same safety checks
apply. A merge can touch several files (the step file and any feature files);
the preview's hash covers all of them, and saving refuses if any changed.

If the only thing stopping a merge is that the two functions' code differs,
the user can merge anyway (``force``) after comparing the code side by side.
Everything else that blocks a merge still does.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gherkin_copilot.indexer import StepDef
from gherkin_copilot.merger import MergePlan, MergeResult, file_changes, function_source, plan_merges
from gherkin_copilot.ui.catalog import editor_link, segments
from gherkin_copilot.ui.projects import Project


def _loc(step: StepDef) -> str:
    return f"{step.file}:{step.line}"


def pair_id(plan: MergePlan) -> str:
    return "|".join(sorted((_loc(plan.keep), _loc(plan.drop))))


def _threshold(value: object) -> float:
    try:
        number = float(value) if isinstance(value, (int, float, str)) else 0.6
    except ValueError:
        number = 0.6
    return min(max(number, 0.3), 0.95)


def _keep(keep: object) -> frozenset[str]:
    """Functions the user chose to keep ("Keep this one instead")."""
    if keep is None:
        return frozenset()
    if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep):
        raise ValueError("keep must be a list of function names.")
    return frozenset(keep)


def _plans(project: Project, threshold: object, keep: object = None) -> MergeResult:
    return plan_merges(Path(project.steps), _threshold(threshold), _keep(keep), features=Path(project.features))


def _step(step: StepDef, uses: int, folder: Path, code: bool = False) -> dict[str, Any]:
    return {**asdict(step), "segments": segments(step), "uses": uses, "open_url": editor_link(folder, step),
            "code": function_source(folder.resolve(), step) if code else None}


def overview(project: Project, threshold: object, keep: object = None) -> dict[str, Any]:
    result = _plans(project, threshold, keep)
    return {
        "threshold": _threshold(threshold),
        "pairs": [{
            "id": pair_id(p),
            "score": p.score,
            "ok": p.ok,
            "problems": p.problems,
            "can_force": p.can_force,  # only their code differs: offer "Merge anyway"
            "differences": p.differences,
            "keep": _step(p.keep, p.keep_uses, Path(project.steps), code=p.can_force),
            "drop": _step(p.drop, p.drop_uses, Path(project.steps), code=p.can_force),
            "rewrites": len(p.edits),
        } for p in result.plans],
        "skipped": [[_loc(a), _loc(b)] for a, b in result.skipped],
    }


def _shown(path: Path, project: Project) -> str:
    for folder in (Path(project.steps), Path(project.features)):
        if folder in path.parents:
            return path.relative_to(folder).as_posix()
    return str(path)


def _hash(changes: dict[Path, tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for path in sorted(changes):
        digest.update(f"{path}\0{changes[path][0]}\0".encode())
    return digest.hexdigest()


def _one(project: Project, threshold: object, pair: str, keep: object,
         force: object = False) -> dict[Path, tuple[str, str]]:
    plan = next((p for p in _plans(project, threshold, keep).plans if pair_id(p) == pair), None)
    if plan is None:
        raise ValueError("These steps have changed since the list was loaded. Refresh and try again.")
    if not plan.ok and not (force is True and plan.can_force):
        raise ValueError("This pair can't be merged automatically: " + "; ".join(plan.problems))
    return file_changes([plan], Path(project.steps).resolve())


def preview(project: Project, threshold: object, pair: str, keep: object, force: object = False) -> dict[str, Any]:
    changes = _one(project, threshold, pair, keep, force)
    files = []
    for path, (before, after) in sorted(changes.items()):
        shown = _shown(path, project)
        files.append({"file": shown, "diff": list(difflib.unified_diff(
            before.splitlines(), after.splitlines(), f"a/{shown}", f"b/{shown}", lineterm=""))})
    return {"files": files, "hash": _hash(changes)}


def apply(project: Project, threshold: object, pair: str, keep: object,
          expected_hash: object, force: object = False) -> dict[str, tuple[str, str]]:
    """Write the merge; returns shown path -> (before, after) for each file changed."""
    if not isinstance(expected_hash, str):
        raise ValueError("Preview the merge before applying it.")
    changes = _one(project, threshold, pair, keep, force)  # re-planned against the files as they are now
    if _hash(changes) != expected_hash:
        raise ValueError("Some of these files changed since the preview. Preview again to see the latest version.")
    for path, (_, after) in changes.items():
        path.write_text(after, encoding="utf-8")
    return {_shown(p, project): pair for p, pair in changes.items()}
