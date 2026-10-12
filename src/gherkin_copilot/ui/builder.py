"""Back end of the scenario builder: check a draft, save it, add stubs for its missing steps.

Every write is planned first and shown to the user as a diff. Saving
re-plans against the file as it is now and refuses if the file changed since
the preview (compared by hash), so nothing is written that the user didn't see.
Writes only go to files inside the project's own folders, and only to
``.feature`` files (scenarios) or existing step files (stubs). Stub code is
always generated here from the draft, never taken from the browser.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gherkin_copilot.checker import check_steps, make_stub, parse_feature_text
from gherkin_copilot.indexer import build_index, index_file, iter_python_files
from gherkin_copilot.ui.catalog import segments
from gherkin_copilot.ui.projects import Project

STEP_START = re.compile(r"^(Given|When|Then|And|But|\*)\s+\S")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,80}$")
MAX_LINES = 200


@dataclass
class FileChange:
    path: Path
    shown: str  # path relative to the project folder, for display
    before: str
    after: str
    creates: bool

    @property
    def hash(self) -> str:
        return _hash(self.before)

    def describe(self) -> dict[str, Any]:
        diff = difflib.unified_diff(self.before.splitlines(), self.after.splitlines(),
                                    f"a/{self.shown}", f"b/{self.shown}", lineterm="")
        return {"file": self.shown, "creates": self.creates, "hash": self.hash, "diff": list(diff)}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- the draft --------------------------------------------------------------

def clean_lines(lines: object) -> list[str]:
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("Steps must be a list of text lines.")
    if len(lines) > MAX_LINES:
        raise ValueError(f"A scenario can have at most {MAX_LINES} steps.")
    cleaned = [" ".join(line.split()) for line in lines]
    for line in cleaned:
        if not STEP_START.match(line):
            raise ValueError(f"Every step must start with Given, When, Then, And or But: {line or '(empty)'}")
    return cleaned


def _one_line(text: str, what: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise ValueError(f"Give the {what} a name.")
    if len(text) > 200:
        raise ValueError(f"Keep the {what} name under 200 characters.")
    return text


def check(project: Project, lines: list[str], examples: object = None) -> list[dict[str, Any]]:
    """Status of each draft line, in order: ok / missing / ambiguous, with suggestions and a stub.

    For an outline, pass its Examples tables: placeholders are filled from the first row.
    """
    from gherkin_copilot.ui.editor import outline_check_text  # editor builds on this module

    text = outline_check_text(lines, examples)
    results = check_steps(parse_feature_text(text), build_index(Path(project.steps)))
    return [{
        "status": r.status,
        "bound": [{"file": s.file, "line": s.line, "function": s.function} for s in r.bound],
        "suggestions": [{"score": m.score, **asdict(m.step), "segments": segments(m.step)} for m in r.suggestions],
        "stub": make_stub(r.step) if r.status == "missing" else None,
    } for r in results]


# --- files ------------------------------------------------------------------

def _inside(folder: Path, relative: str, suffix: str) -> Path:
    if not relative or "\\" in relative or relative.startswith("/"):
        raise ValueError("Pick a file from the list.")
    path = (folder / relative).resolve()
    if folder not in path.parents or path.suffix != suffix:
        raise ValueError("That file is outside the project.")
    return path


def project_files(project: Project) -> dict[str, list[str]]:
    features, steps = Path(project.features), Path(project.steps)
    return {
        "features": sorted(str(p.relative_to(features)) for p in features.rglob("*.feature")
                           if ".venv" not in p.parts),
        "steps": sorted(str(p.relative_to(steps)) for p in iter_python_files(steps) if index_file(p)),
    }


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def plan_stubs(project: Project, file: str, lines: object, only: object = None,
               examples: object = None) -> FileChange:
    """Stubs for the draft's missing lines; ``only`` limits them to those positions.

    The whole draft is always checked, so an "And" line gets the keyword of the line above it.
    """
    folder = Path(project.steps)
    path = _inside(folder, file, ".py")
    if not path.is_file() or not index_file(path):
        raise ValueError("Pick one of the project's step files.")
    if only is not None and (not isinstance(only, list) or not all(isinstance(i, int) for i in only)):
        raise ValueError("Choose which steps to add.")
    results = check(project, clean_lines(lines), examples)
    stubs = list(dict.fromkeys(r["stub"] for i, r in enumerate(results) if r["stub"] and (only is None or i in only)))
    if not stubs:
        raise ValueError("Every step in this scenario already exists.")

    before = path.read_text(encoding="utf-8")
    tree = ast.parse(before)
    existing = _module_names(tree)
    renamed = []
    for stub in stubs:  # don't shadow a function that's already in the file
        found = re.search(r"^def (\w+)\(", stub, re.M)
        name = found.group(1) if found else "step"
        new, n = name, 2
        while new in existing:
            new, n = f"{name}_{n}", n + 1
        existing.add(new)
        renamed.append(stub.replace(f"def {name}(", f"def {new}(", 1))

    used = {kw for kw in ("given", "when", "then") if any(s.startswith(f"@{kw}(") for s in renamed)}
    used |= {"parsers"} if any("parsers." in s for s in renamed) else set()
    missing = sorted(used - _module_names(tree))
    out = before.rstrip("\n").splitlines()
    if missing:
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        at = (imports[-1].end_lineno or 0) if imports else 0
        out.insert(at, f"from pytest_bdd import {', '.join(missing)}")
    after = "\n".join(out) + "\n\n\n" + "\n\n\n".join(s.rstrip("\n") for s in renamed) + "\n"
    return FileChange(path, str(path.relative_to(folder)), before, after, creates=False)


def apply_change(change: FileChange, expected_hash: str) -> None:
    current = change.path.read_text(encoding="utf-8") if change.path.exists() else ""
    if change.creates and change.path.exists():
        raise ValueError(f"{change.shown} was created by someone else in the meantime. Preview again.")
    if _hash(current) != expected_hash or change.before != current:
        raise ValueError(f"{change.shown} changed since the preview. Preview again to see the latest version.")
    change.path.parent.mkdir(parents=True, exist_ok=True)
    change.path.write_text(change.after, encoding="utf-8")
