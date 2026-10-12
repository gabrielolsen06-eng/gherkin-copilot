"""Match Gherkin step text against indexed step definitions.

Two kinds of matching:

* exact: would this step text actually bind to the definition at runtime?
  (parse-style patterns are converted to regexes, regex patterns are used as is)
* fuzzy: how similar is the wording, ignoring argument values and filler words?
  Used for "did you mean ...?" suggestions and duplicate detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations

from gherkin_copilot.indexer import StepDef

GHERKIN_KEYWORD = re.compile(r"^\s*(given|when|then|and|but|\*)\s+", re.IGNORECASE)
PARSE_FIELD = re.compile(r"\{([^{}]*)\}")
REGEX_GROUP = re.compile(r"\((?:\?P<\w+>)?[^()]*\)")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
WORD = re.compile(r"<arg>|[a-z0-9]+")
FILLER = {"a", "an", "the", "i", "is", "am", "are", "be", "to", "on", "in", "of", "my", "that"}
ARG = "<arg>"

PARSE_TYPES = {
    "d": r"-?\d+",
    "n": r"-?[\d,]+",
    "f": r"-?\d+\.\d+",
    "g": r"-?\d+(?:\.\d+)?",
    "w": r"\w+",
    "W": r"\W+",
    "S": r"\S+",
}


@dataclass(frozen=True)
class Match:
    step: StepDef
    score: float
    exact: bool


@dataclass(frozen=True)
class DuplicatePair:
    first: StepDef
    second: StepDef
    score: float


def strip_keyword(text: str) -> str:
    return GHERKIN_KEYWORD.sub("", text.strip(), count=1).strip()


def _parse_to_regex(pattern: str) -> str:
    out, pos = [], 0
    for m in PARSE_FIELD.finditer(pattern):
        out.append(re.escape(pattern[pos : m.start()]))
        _, _, fmt = m.group(1).partition(":")
        out.append(f"({PARSE_TYPES.get(fmt, '.+?')})")
        pos = m.end()
    out.append(re.escape(pattern[pos:]))
    return "".join(out)


def to_regex(step: StepDef) -> re.Pattern[str] | None:
    source = step.pattern if step.kind == "regex" else _parse_to_regex(step.pattern)
    source = source.removeprefix("^").removesuffix("$")
    try:
        return re.compile(rf"^{source}$")
    except re.error:
        return None


def is_exact_match(text: str, step: StepDef) -> bool:
    regex = to_regex(step)
    return bool(regex and regex.match(strip_keyword(text)))


def normalize_pattern(step: StepDef) -> str:
    """Lowercase the pattern and replace every placeholder with <arg>."""
    text = step.pattern
    if step.kind == "regex":
        text = text.removeprefix("^").removesuffix("$")
        while REGEX_GROUP.search(text):
            text = REGEX_GROUP.sub(ARG, text)
        text = text.replace("\\", "")
    else:
        text = PARSE_FIELD.sub(ARG, text)
    return _clean(QUOTED.sub(lambda q: ARG if ARG in q.group() else q.group(), text))


def normalize_text(text: str) -> str:
    """Lowercase step text and replace literal values (quotes, numbers) with <arg>."""
    text = QUOTED.sub(ARG, strip_keyword(text))
    return _clean(NUMBER.sub(ARG, text))


def _clean(text: str) -> str:
    words = [w for w in WORD.findall(text.lower()) if w not in FILLER]
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    """Blend of character similarity and word overlap, from 0.0 to 1.0."""
    if not a or not b:
        return 0.0
    chars = SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    words = len(wa & wb) / len(wa | wb)
    return round(0.5 * chars + 0.5 * words, 3)


def find(text: str, steps: list[StepDef], limit: int = 5, min_score: float = 0.4) -> list[Match]:
    """Exact matches first, then the closest fuzzy matches."""
    query = normalize_text(text)
    matches = []
    for step in steps:
        if is_exact_match(text, step):
            matches.append(Match(step, 1.0, True))
            continue
        score = similarity(query, normalize_pattern(step))
        if score >= min_score:
            matches.append(Match(step, score, False))
    matches.sort(key=lambda m: (not m.exact, -m.score, m.step.location))
    return matches[:limit]


def find_duplicates(steps: list[StepDef], threshold: float = 0.6) -> list[DuplicatePair]:
    """Pairs of step definitions whose wording is suspiciously similar.

    Every pair is a candidate, so the expensive character comparison is skipped
    whenever a cheap upper bound already shows the pair can't reach the threshold:
    first from word overlap alone (character similarity is at most 1), then from
    SequenceMatcher's quick estimates. The results are the same as comparing every
    pair in full; 1,000 steps take seconds instead of most of a minute.
    """
    slack = 0.0005  # scores are rounded to 3 places, so keep bounds a hair generous
    normalized = [(s, n, set(n.split())) for s in steps for n in [normalize_pattern(s)]]
    pairs = []
    for (a, na, wa), (b, nb, wb) in combinations(normalized, 2):
        if not na or not nb:
            continue
        words = len(wa & wb) / len(wa | wb)
        if 0.5 + 0.5 * words + slack < threshold:
            continue
        matcher = SequenceMatcher(None, na, nb)
        if (0.5 * matcher.real_quick_ratio() + 0.5 * words + slack < threshold
                or 0.5 * matcher.quick_ratio() + 0.5 * words + slack < threshold):
            continue
        score = round(0.5 * matcher.ratio() + 0.5 * words, 3)
        if score >= threshold:
            pairs.append(DuplicatePair(a, b, score))
    pairs.sort(key=lambda p: -p.score)
    return pairs
