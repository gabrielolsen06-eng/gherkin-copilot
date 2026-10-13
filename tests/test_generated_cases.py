"""Generated inputs (seeded, so every run is the same) plus the parser edge cases from the review (M6, L7-L9)."""

import random
import re

import pytest

from gherkin_copilot.checker import parse_feature_text
from gherkin_copilot.indexer import StepDef
from gherkin_copilot.matcher import _parse_to_regex, is_exact_match, normalize_pattern
from gherkin_copilot.report import Feature, Scenario, render_html

WORDS = ["user", "cart", "page", "product", "order", "total", "badge", "login", "item", "list", "error", "price"]
FIXED = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]  # fixed pattern words, never used as values
KEYWORDS = ["Given", "When", "Then", "And", "But"]


def random_feature(rng):
    """A feature file with steps mixed with the things a parser must skip; returns (text, expected steps)."""
    lines, expected = ["Feature: generated", "  Some description text", "  Given a user story, not a step"], []
    for s in range(rng.randint(1, 4)):
        lines += ["", f"  @tag{s}", f"  Scenario: scenario {s}"]
        for _ in range(rng.randint(1, 6)):
            text = " ".join(rng.sample(WORDS, rng.randint(2, 5)))
            lines.append(f"    {rng.choice(KEYWORDS)} {text}")
            expected.append((len(lines), text))
            extra = rng.choice(["", "table", "doc", "fence", "comment"])
            if extra == "table":
                lines += ["      | a | b |", "      | 1 | 2 |"]
            elif extra == "doc":
                lines += ['      """', "      Given this is not a step", '      """']
            elif extra == "fence":  # a ``` doc string that contains """: must not end at """ (L7)
                lines += ["      ```", '      """', "      When this is not a step either", "      ```"]
            elif extra == "comment":
                lines.append("    # Then not a step")
    return "\n".join(lines) + "\n", expected


@pytest.mark.parametrize("seed", range(40))
def test_parser_finds_exactly_the_steps(seed):
    text, expected = random_feature(random.Random(seed))
    assert [(s.line, s.text) for s in parse_feature_text(text)] == expected


@pytest.mark.parametrize("seed", range(40))
def test_parse_patterns_match_their_own_filled_in_text(seed):
    """Any pattern with {fields}, filled in with suitable values, binds; a changed fixed word doesn't."""
    rng = random.Random(seed)
    parts, filled = [rng.choice(FIXED)], []
    filled.append(parts[0])
    for i in range(rng.randint(2, 6)):
        word = rng.choice(FIXED)
        kind = rng.choice(["word", "text", "number", "quoted", "brace"])
        if kind == "text":
            parts.append(f"{{f{i}}}")
            filled.append(" ".join(rng.sample(WORDS, 2)))
        elif kind == "number":
            parts.append(f"{{n{i}:d}}")
            filled.append(str(rng.randint(-50, 5000)))
        elif kind == "quoted":
            parts.append(f'"{{q{i}}}"')
            filled.append(f'"{rng.choice(WORDS)} {rng.randint(0, 9)}"')
        elif kind == "brace":  # parse's escaped literal braces (L8)
            parts.append("{{" + word + "}}")
            filled.append("{" + word + "}")
        else:
            parts.append(word)
            filled.append(word)
    step = StepDef("given", " ".join(parts), "parse", "f", "s.py", 1)
    text = " ".join(filled)
    assert is_exact_match(f"Given {text}", step), (step.pattern, text)
    assert not is_exact_match(f"Given zulu {text.split(' ', 1)[1]}", step)  # the first fixed word changed
    re.compile(_parse_to_regex(step.pattern))


def test_escaped_braces_are_literal():
    step = StepDef("given", 'the json is {{"id": {id:d}}}', "parse", "f", "s.py", 1)
    assert is_exact_match('Given the json is {"id": 7}', step)
    assert not is_exact_match('Given the json is "id": 7', step)  # used to match: {{ and }} were treated loosely
    assert normalize_pattern(step) == "json id <arg>"  # the braces are text, the field is a placeholder


def test_trace_command_is_absolute(tmp_path, monkeypatch):
    trace = tmp_path / "artifacts" / "t" / "trace.zip"
    trace.parent.mkdir(parents=True)
    trace.write_bytes(b"")
    scenario = Scenario("S", "Scenario", "t", 1, [], [], {"traces": [trace]})
    monkeypatch.chdir(tmp_path / "artifacts")  # the report used to make the path relative to here
    html = render_html([Feature("F", "f.feature", "", [scenario])], tmp_path, "cucumber.json")
    assert f'show-trace --host 127.0.0.1 --port 0 "{trace.resolve()}"' in html
