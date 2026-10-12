"""Find step definitions in Python source files without importing them.

Supports the decorator styles used by pytest-bdd and behave:

    @given("I am on the login page")                  # plain string
    @when(parsers.parse("I add {count:d} items"))     # parse / cfparse
    @then(parsers.re(r"the total is (?P<total>\\d+)")) # regex
    @step("I wait {seconds} seconds")                 # behave catch-all

Source files are parsed with ``ast``, so the project's dependencies don't
need to be installed for indexing to work.
"""

from __future__ import annotations

import ast
import json
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

STEP_KEYWORDS = {"given", "when", "then", "step"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist"}


@dataclass(frozen=True)
class StepDef:
    keyword: str  # given / when / then / step
    pattern: str  # the step text or pattern as written
    kind: str  # "string", "parse" or "regex"
    function: str
    file: str
    line: int

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class Index:
    """A saved index: the steps, plus where and when they were scanned (to spot a stale index)."""

    steps: list[StepDef]
    root: Path | None = None
    created: float = field(default=0.0)


def _decorator_name(node: ast.expr) -> str | None:
    """Return 'given' for @given, @pytest_bdd.given, @behave.given, etc."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _string_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _pattern_from_arg(arg: ast.expr) -> tuple[str, str] | None:
    """Extract (pattern, kind) from a decorator's first argument."""
    text = _string_value(arg)
    if text is not None:
        return text, "string"
    # parsers.parse("..."), parsers.cfparse("..."), parsers.re("..."), parse("...")
    if isinstance(arg, ast.Call) and arg.args:
        inner = _string_value(arg.args[0])
        name = _decorator_name(arg.func)
        if inner is None or name is None:
            return None
        if name in {"re", "regex"}:
            return inner, "regex"
        if name in {"parse", "cfparse"}:
            return inner, "parse"
        if name == "string":
            return inner, "string"
    return None


def index_file(path: Path, root: Path | None = None) -> list[StepDef]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    shown_path = path.relative_to(root).as_posix() if root else str(path)
    # pytest-bdd matches a plain string literally; behave reads {name} in a plain string as a placeholder
    plain_strings_are_literal = _imports(tree, "pytest_bdd") and not _imports(tree, "behave")
    steps: list[StepDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not deco.args:
                continue
            keyword = _decorator_name(deco.func)
            if keyword is None or keyword.lower() not in STEP_KEYWORDS:
                continue
            extracted = _pattern_from_arg(deco.args[0])
            if extracted is None:
                continue
            pattern, kind = extracted
            if kind == "string" and "{" in pattern and "}" in pattern and not plain_strings_are_literal:
                kind = "parse"
            steps.append(
                StepDef(
                    keyword=keyword.lower(),
                    pattern=pattern,
                    kind=kind,
                    function=node.name,
                    file=shown_path,
                    line=deco.lineno,
                )
            )
    return steps


def _imports(tree: ast.Module, module: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == module for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == module:
            return True
    return False


def iter_project_files(root: Path, pattern: str) -> Iterator[Path]:
    """Files under root, skipping virtual environments, build output and caches *inside* the project.

    Only the part of the path below root is checked, so a project that itself lives in a
    folder called build/ or dist/ is still scanned.
    """
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob(pattern)):
        if not any(part in SKIP_DIRS or part.startswith(".venv") for part in path.relative_to(root).parts[:-1]):
            yield path


def iter_python_files(root: Path) -> Iterator[Path]:
    return iter_project_files(root, "*.py")


def build_index(root: Path) -> list[StepDef]:
    root = root.resolve()
    base = root if root.is_dir() else root.parent
    steps: list[StepDef] = []
    for path in iter_python_files(root):
        steps.extend(index_file(path, base))
    return steps


def save_index(steps: list[StepDef], out: Path, root: Path | None = None) -> None:
    data = {"root": str(root.resolve()) if root else None, "created": time.time(), "steps": [asdict(s) for s in steps]}
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_index(path: Path) -> Index:
    """Read a saved index. Raises ValueError with a readable message if the file isn't one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):  # written by an older version: steps only
            return Index([StepDef(**item) for item in data])
        root = data.get("root")
        return Index([StepDef(**item) for item in data["steps"]], Path(root) if root else None,
                     float(data.get("created") or 0))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:  # ValueError covers bad JSON and bad numbers
        raise ValueError(f"{path} isn't a valid index file. Run `gherkin-copilot index` to rebuild it.") from exc
