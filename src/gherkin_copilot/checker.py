"""Check .feature files against the step index and draft stubs for missing steps.

The feature parser is deliberately small: it understands Feature, Background,
Scenario, Scenario Outline / Examples, And / But, tags, comments, doc strings
and data tables, which covers what step matching needs.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gherkin_copilot.indexer import StepDef, iter_project_files
from gherkin_copilot.matcher import Match, find, is_exact_match

STEP_LINE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
SECTION = re.compile(
    r"^(Feature|Background|Scenario Outline|Scenario Template|Scenario|Example|Rule|Examples|Scenarios):")
OUTLINE_PARAM = re.compile(r"<([^<>]+)>")
QUOTED = re.compile(r'"[^"]*"')
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


@dataclass
class FeatureStep:
    keyword: str  # given / when / then (And / But resolved to the previous one)
    text: str  # step text with outline parameters filled from the first Examples row
    raw: str  # the line as written
    file: str
    line: int


@dataclass
class StepResult:
    step: FeatureStep
    bound: list[StepDef]
    suggestions: list[Match] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.bound:
            return "missing"
        return "ambiguous" if len(self.bound) > 1 else "ok"


def parse_feature(path: Path, shown_path: str | None = None) -> list[FeatureStep]:
    """Return every step in a feature file, in order."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} isn't UTF-8 text, so it can't be read as a feature file.") from exc
    return parse_feature_text(text, shown_path or str(path))


def parse_feature_text(text: str, shown_path: str = "<text>") -> list[FeatureStep]:
    lines = text.splitlines()
    steps: list[FeatureStep] = []
    outline: list[FeatureStep] = []  # steps waiting for their Examples table
    previous = "given"
    docstring: str | None = None  # the fence of the doc string we're inside
    section = ""
    examples_header: list[str] | None = None

    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if docstring is None and stripped.startswith(('"""', "```")):
            docstring = stripped[:3]  # only the same fence closes it: a ``` block may contain """
            continue
        if docstring is not None and stripped.startswith(docstring):
            docstring = None
            continue
        if docstring is not None or not stripped or stripped.startswith(("#", "@")):
            continue

        if m := SECTION.match(stripped):
            section = m.group(1)
            examples_header = None
            if section in {"Scenario", "Example", "Background", "Feature", "Rule"}:
                steps.extend(outline)  # outline without Examples: keep the raw text
                outline = []
            continue

        if stripped.startswith("|"):
            if section in {"Examples", "Scenarios"}:
                if examples_header is None:
                    examples_header = cells(stripped)
                elif outline:  # fill outline parameters from the first Examples row
                    values = dict(zip(examples_header, cells(stripped), strict=False))

                    def fill(m: re.Match[str], values: dict[str, str] = values) -> str:
                        return values.get(m.group(1), m.group(0))

                    for s in outline:
                        s.text = OUTLINE_PARAM.sub(fill, s.text)
                    steps.extend(outline)
                    outline = []
            continue  # data tables under a step don't affect matching

        if m := STEP_LINE.match(stripped):
            word, text = m.groups()
            kw = previous if word in {"And", "But", "*"} else word.lower()
            previous = kw
            step = FeatureStep(kw, text.strip(), stripped, shown_path, number)
            if section in {"Scenario Outline", "Scenario Template"}:
                outline.append(step)
            else:
                steps.append(step)
    steps.extend(outline)
    steps.sort(key=lambda s: s.line)
    return steps


def binds(step: FeatureStep, definition: StepDef) -> bool:
    if definition.keyword not in {step.keyword, "step"}:
        return False
    return is_exact_match(step.text, definition)


def check_steps(feature_steps: list[FeatureStep], definitions: list[StepDef]) -> list[StepResult]:
    results = []
    for fs in feature_steps:
        bound = [d for d in definitions if binds(fs, d)]
        suggestions = [] if bound else find(fs.text, definitions, limit=3, min_score=0.5)
        results.append(StepResult(fs, bound, suggestions))
    return results


def iter_feature_files(root: Path) -> Iterator[Path]:
    return iter_project_files(root, "*.feature")


def _function_name(text: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "step"
    if name[0].isdigit() or keyword.iskeyword(name):
        name = f"step_{name}"
    return name[:60].rstrip("_")


def stub_parts(step: FeatureStep) -> tuple[str, list[tuple[str, str]], str]:
    """(pattern, [(parameter, format)], function name) for a new step definition matching this line.

    Quoted values and numbers become parameters: 'I pay "visa" 2 times' -> 'I pay "{text1}" {number2:d} times'.
    """
    params: list[tuple[str, str]] = []

    def quoted(m: re.Match[str]) -> str:
        params.append((f"text{len(params) + 1}", ""))
        return f'"{{{params[-1][0]}}}"'

    def number(m: re.Match[str]) -> str:
        fmt = ":g" if "." in m.group() else ":d"
        params.append((f"number{len(params) + 1}", fmt))
        return f"{{{params[-1][0]}{fmt}}}"

    pattern = NUMBER.sub(number, QUOTED.sub(quoted, step.text.replace("{", "{{").replace("}", "}}")))
    function = _function_name(NUMBER.sub("", OUTLINE_PARAM.sub("", QUOTED.sub("", step.text))))
    return pattern, params, function


def make_stub(step: FeatureStep) -> str:
    """Draft a pytest-bdd step definition, turning quoted values and numbers into parameters."""
    pattern, params, function = stub_parts(step)
    args = ", ".join(["page"] + [p for p, _ in params])
    decorator = f"@{step.keyword}(parsers.parse({pattern!r}))" if params else f"@{step.keyword}({pattern!r})"
    return (
        f"{decorator}\n"
        f"def {function}({args}):\n"
        f"    raise NotImplementedError({('Step not written yet: ' + step.text)!r})\n"
    )
