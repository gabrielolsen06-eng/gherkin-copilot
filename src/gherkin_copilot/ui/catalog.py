"""Describe step definitions for the app: fixed text plus fill-in slots."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gherkin_copilot.indexer import StepDef, build_index
from gherkin_copilot.matcher import PARSE_FIELD

REGEX_GROUP = re.compile(r"\((?:\?P<(\w+)>)?((?:[^()]|\([^()]*\))*)\)")
NUMBER_FORMATS = {"d", "n", "f", "g"}


def segments(step: StepDef) -> list[dict[str, str]]:
    """Split a pattern into [{"text": ...}, {"slot": name, "type": "text"|"number"}, ...]."""
    if step.kind == "string":
        return [{"text": step.pattern}]
    out: list[dict[str, str]] = []
    pattern = step.pattern
    unescape: Callable[[str], str]
    if step.kind == "regex":
        pattern = pattern.removeprefix("^").removesuffix("$")
        finder, unescape = REGEX_GROUP, _readable
    else:
        finder, unescape = PARSE_FIELD, str  # parse fields need no unescaping
    pos = 0
    for n, m in enumerate(finder.finditer(pattern), start=1):
        if m.start() > pos:
            out.append({"text": unescape(pattern[pos : m.start()])})
        if step.kind == "regex":
            name, body = m.group(1) or f"value{n}", m.group(2)
            kind = "number" if body.lstrip("-?").startswith("\\d") else "text"
        else:
            name, _, fmt = m.group(1).partition(":")
            name, kind = name or f"value{n}", "number" if fmt in NUMBER_FORMATS else "text"
        out.append({"slot": name, "type": kind})
        pos = m.end()
    if pos < len(pattern):
        out.append({"text": unescape(pattern[pos:])})
    return out


def _readable(regex_text: str) -> str:
    """Regex text as people read it: "items?" -> "items", "\\." -> "."."""
    return re.sub(r"\\(.)", r"\1", re.sub(r"(\w)\?", r"\1", regex_text))


def editor_url(path: str, line: int) -> str:
    """vscode://file/... opens the file at that line in VS Code: /Users/me/a.py and C:/Users/me/a.py alike."""
    return f"vscode://file{'' if path.startswith('/') else '/'}{quote(path, safe='/:')}:{line}"


def editor_link(folder: Path, step: StepDef) -> str:
    """Where to open a step's code. The link is built here from the project's own folder, never taken from
    the browser, and following it is the browser's job (it asks before opening VS Code): the app starts nothing."""
    base = folder.resolve()
    base = base if base.is_dir() else base.parent  # index paths are relative to the folder a step file is in
    return editor_url((base / step.file).as_posix(), step.line)


def step_info(folder: Path, step: StepDef) -> dict[str, Any]:
    """A step as the page shows it: its fields, its wording as text and slots, and a link to its code."""
    return {**asdict(step), "segments": segments(step), "open_url": editor_link(folder, step)}


def describe(folder: Path) -> list[dict[str, Any]]:
    return [step_info(folder, s) for s in build_index(folder)]
