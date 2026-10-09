"""Describe step definitions for the app: fixed text plus fill-in slots."""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

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
    if step.kind == "regex":
        pattern = pattern.removeprefix("^").removesuffix("$")
        finder, unescape = REGEX_GROUP, _readable
    else:
        finder, unescape = PARSE_FIELD, (lambda s: s)
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


def describe(folder: Path) -> list[dict]:
    return [{**asdict(s), "segments": segments(s)} for s in build_index(folder)]
