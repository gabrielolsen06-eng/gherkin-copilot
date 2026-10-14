"""Check .feature files against the step index and draft stubs for missing steps.

Feature files are read with ``gherkin.parse``, the same parser the app's editor
uses: Feature, Rule, Background, Scenario, Scenario Outline / Examples, tags,
comments, doc strings and data tables.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gherkin_copilot import gherkin
from gherkin_copilot.indexer import StepDef, iter_project_files
from gherkin_copilot.matcher import Match, find, is_exact_match

STEP_LINE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
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
    suggestions: list[Match] = field(default_factory=list[Match])

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
    """Every step, read with the same parser the app's editor uses, so both always agree."""
    lines = text.splitlines()
    steps: list[FeatureStep] = []
    previous = "given"
    for block in gherkin.parse(text).blocks:
        first_row = next((e for e in block.examples if e.rows), None) if block.outline else None
        values = dict(zip(first_row.header, first_row.rows[0], strict=False)) if first_row else {}
        for step in block.steps:
            keyword_ = previous if step.keyword in {"And", "But", "*"} else step.keyword.lower()
            previous = keyword_
            steps.append(FeatureStep(keyword_, _fill(step.text, values), lines[step.line - 1].strip(), shown_path,
                                     step.line))
    return steps


def _fill(text: str, values: dict[str, str]) -> str:
    """An outline's <placeholders>, filled from its first Examples row."""
    return OUTLINE_PARAM.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def binds(step: FeatureStep, definition: StepDef) -> bool:
    if definition.keyword not in {step.keyword, "step"}:
        return False
    return is_exact_match(step.text, definition)


def check_steps(feature_steps: list[FeatureStep], definitions: list[StepDef]) -> list[StepResult]:
    results: list[StepResult] = []
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


def make_stub(step: FeatureStep, fixture: str = "page") -> str:
    """Draft a pytest-bdd step definition, turning quoted values and numbers into parameters.
    ``fixture`` is what the step works on: Playwright's ``page``, or ``app`` for a desktop app."""
    pattern, params, function = stub_parts(step)
    args = ", ".join([fixture] + [p for p, _ in params])
    decorator = f"@{step.keyword}(parsers.parse({pattern!r}))" if params else f"@{step.keyword}({pattern!r})"
    return (
        f"{decorator}\n"
        f"def {function}({args}):\n"
        f"    raise NotImplementedError({('Step not written yet: ' + step.text)!r})\n"
    )
