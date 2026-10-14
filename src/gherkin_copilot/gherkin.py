"""Read a .feature file into scenarios with their exact line spans, and write scenarios back.

The editor replaces one scenario at a time, so the rest of the file (comments,
spacing, other scenarios) is left exactly as it was. A scenario's span runs
from its first tag line to its last non-blank line; comments between scenarios
stay outside every span. Comments just above a step travel with that step; any
other line in a span that can't be written back is listed in ``Block.unkept``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BLOCK = re.compile(r"^(Background|Scenario Outline|Scenario Template|Scenario|Example):\s*(.*)$")
EXAMPLES = re.compile(r"^(Examples|Scenarios):\s*(.*)$")
FEATURE = re.compile(r"^Feature:\s*(.*)$")
RULE = re.compile(r"^Rule:\s*(.*)$")
STEP = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
CELL_SPLIT = re.compile(r"(?<!\\)\|")
OUTLINES = {"Scenario Outline", "Scenario Template"}


@dataclass
class Step:
    keyword: str  # as written: Given / When / Then / And / But / *
    text: str
    line: int
    attachment: list[str] = field(default_factory=list[str])  # data table / doc string, indented relative to the step
    comments: list[str] = field(default_factory=list[str])  # "# ..." lines just above the step


@dataclass
class Examples:
    keyword: str
    name: str
    tags: list[str]
    header: list[str]
    rows: list[list[str]]


@dataclass
class Block:
    """A Background, Scenario or Scenario Outline."""

    kind: str  # keyword as written, e.g. "Scenario Outline"
    name: str
    tags: list[str]
    description: list[str]
    steps: list[Step]
    examples: list[Examples]
    start: int  # first line (tags included), 1-based
    end: int  # last non-blank line
    indent: str
    unkept: list[int] = field(default_factory=list[int])  # lines in the span that render() can't write back
    invalid: list[int] = field(default_factory=list[int])  # of those, the ones pytest-bdd rejects (not Gherkin)

    @property
    def outline(self) -> bool:
        return self.kind in OUTLINES


@dataclass
class FeatureFile:
    name: str
    tags: list[str]
    description: list[str]
    blocks: list[Block]

    @property
    def background(self) -> Block | None:
        return next((b for b in self.blocks if b.kind == "Background"), None)

    @property
    def scenarios(self) -> list[Block]:
        return [b for b in self.blocks if b.kind != "Background"]


def cells(row: str) -> list[str]:
    inner = row.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT.split(inner)]


def parse(text: str) -> FeatureFile:
    feature = FeatureFile("", [], [], [])
    lines = text.splitlines()
    tags: list[str] = []
    tag_start: int | None = None
    current: Block | None = None
    examples: Examples | None = None
    step: Step | None = None
    in_doc: str | None = None
    in_feature_description = False
    comments: list[int] = []  # comment lines seen since the last line that belongs to a block

    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if in_doc is not None and step is not None and current is not None:  # inside a doc string: keep it verbatim
            step.attachment.append(_relative(raw, current.indent + "  "))
            current.end = number
            if stripped.startswith(in_doc):
                in_doc = None
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            comments.append(number)
            continue
        if stripped.startswith("@"):
            tags += stripped.split()
            tag_start = tag_start or number
            continue

        if m := FEATURE.match(stripped):
            feature.name, feature.tags = m.group(1), tags
            tags, tag_start, in_feature_description, comments = [], None, True, []
            continue
        if RULE.match(stripped):
            current, examples, step, tags, tag_start, in_feature_description = None, None, None, [], None, False
            comments = []
            continue
        if m := BLOCK.match(stripped):
            in_feature_description = False
            current = Block(m.group(1), m.group(2).strip(), tags, [], [], [], tag_start or number, number,
                            raw[: len(raw) - len(raw.lstrip())])
            current.unkept = [n for n in comments if n > current.start]  # between its tags and its keyword
            feature.blocks.append(current)
            examples, step, tags, tag_start, comments = None, None, [], None, []
            continue
        if in_feature_description:
            feature.description.append(stripped)
            continue
        if current is None:
            continue
        current.end = number
        step_match = STEP.match(stripped)
        if not step_match:  # comments are only kept just above a step
            current.unkept += comments
            comments = []
        if m := EXAMPLES.match(stripped):
            examples = Examples(m.group(1), m.group(2).strip(), tags, [], [])
            current.examples.append(examples)
            step, tags, tag_start = None, [], None
            continue
        if stripped.startswith("|"):
            if examples is not None:
                if not examples.header:
                    examples.header = cells(stripped)
                else:
                    examples.rows.append(cells(stripped))
            elif step is not None:
                step.attachment.append(_relative(raw, current.indent + "  "))
            else:
                current.unkept.append(number)
                current.invalid.append(number)
            continue
        if stripped.startswith(('"""', "```")) and step is not None:
            in_doc = stripped[:3]
            step.attachment.append(_relative(raw, current.indent + "  "))
            continue
        if step_match:
            step = Step(step_match.group(1), step_match.group(2).strip(), number,
                        comments=[lines[n - 1].strip() for n in comments])
            current.steps.append(step)
            comments = []
            continue
        if not current.steps and not current.examples:
            current.description.append(stripped)
        else:
            current.unkept.append(number)
            if examples is None or examples.header:  # text right under "Examples:" is its description, which is valid
                current.invalid.append(number)
    return feature


def _relative(raw: str, step_indent: str) -> str:
    """Indentation of an attached line relative to its step, so it can be re-indented."""
    return raw[len(step_indent):] if raw.startswith(step_indent) else raw.lstrip()


# --- writing ------------------------------------------------------------------

def _row(values: list[str], widths: list[int]) -> str:
    padded = [v.replace("|", "\\|").ljust(w) for v, w in zip(values, widths, strict=True)]
    return "| " + " | ".join(padded) + " |"


def render_table(header: list[str], rows: list[list[str]], indent: str) -> list[str]:
    table = [header, *[(r + [""] * len(header))[: len(header)] for r in rows]]
    widths = [max(len(r[i].replace("|", "\\|")) for r in table) for i in range(len(header))]
    return [indent + _row(r, widths) for r in table]


def render(block: Block) -> str:
    """The block as Gherkin text, at its own indentation, ending with a newline."""
    i = block.indent
    out: list[str] = []
    if block.tags:
        out.append(i + " ".join(block.tags))
    out.append(f"{i}{block.kind}: {block.name}".rstrip())
    out += [f"{i}  {line}" for line in block.description]
    for step in block.steps:
        out += [f"{i}  {comment}" for comment in step.comments]
        out.append(f"{i}  {step.keyword} {step.text}")
        out += [f"{i}  {line}".rstrip() if line.strip() else "" for line in step.attachment]
    for ex in block.examples:
        out.append("")
        if ex.tags:
            out.append(f"{i}  " + " ".join(ex.tags))
        out.append(f"{i}  {ex.keyword}: {ex.name}".rstrip())
        if ex.header:
            out += render_table(ex.header, ex.rows, i + "    ")
    return "\n".join(out) + "\n"


def replace_span(text: str, start: int, end: int, new_block: str | None) -> str:
    """Swap lines start..end (1-based, inclusive) for new text, or remove them (and one blank line)."""
    lines = text.splitlines(keepends=True)
    if new_block is None:
        del lines[start - 1 : end]
        while start - 1 < len(lines) and not lines[start - 1].strip() and start > 1 and not lines[start - 2].strip():
            del lines[start - 1]  # don't leave a double gap
        out = "".join(lines)
        return out.rstrip("\n") + "\n" if out.strip() else out
    lines[start - 1 : end] = [new_block]
    return "".join(lines)


def insert_after(text: str, end: int, new_block: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines[end:end] = ["\n", new_block]
    return "".join(lines)
