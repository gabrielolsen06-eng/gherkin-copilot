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
from dataclasses import asdict, dataclass
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

    shown_path = str(path.relative_to(root)) if root else str(path)
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
            # behave defaults to parse-style placeholders for plain strings
            if kind == "string" and "{" in pattern and "}" in pattern:
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


def iter_python_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*.py")):
        if not any(part in SKIP_DIRS for part in path.parts):
            yield path


def build_index(root: Path) -> list[StepDef]:
    root = root.resolve()
    base = root if root.is_dir() else root.parent
    steps: list[StepDef] = []
    for path in iter_python_files(root):
        steps.extend(index_file(path, base))
    return steps


def save_index(steps: list[StepDef], out: Path) -> None:
    out.write_text(json.dumps([asdict(s) for s in steps], indent=2) + "\n", encoding="utf-8")


def load_index(path: Path) -> list[StepDef]:
    return [StepDef(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
