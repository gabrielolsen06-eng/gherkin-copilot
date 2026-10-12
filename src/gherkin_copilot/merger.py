"""Merge near-duplicate step definitions.

For each pair that ``dupes`` reports, one step is kept and the other is removed:

* feature-file lines that use the removed step are rewritten into the kept
  step's wording, carrying their values across ("Onesie" stays "Onesie");
* the removed step's decorator and function are deleted.

A pair is only merged when that can't change what the tests do: both steps
use the same keyword, their decorators have the same options (such as
``target_fixture``), their functions have identical parameters and bodies,
the removed function isn't called from anywhere else, and every rewritten
line binds to the kept step. Anything else is reported and left alone.

Planning never writes; ``apply`` writes the planned file contents.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gherkin_copilot.checker import STEP_LINE, FeatureStep, binds, iter_feature_files, parse_feature
from gherkin_copilot.indexer import StepDef, build_index, iter_python_files
from gherkin_copilot.matcher import PARSE_FIELD, find_duplicates, is_exact_match

REGEX_NAMED_GROUP = re.compile(r"\(\?P<(\w+)>")


@dataclass
class LineEdit:
    file: Path
    line: int
    old: str
    new: str


@dataclass
class MergePlan:
    keep: StepDef
    drop: StepDef
    score: float
    keep_uses: int = 0  # feature lines using each step before the merge
    drop_uses: int = 0
    edits: list[LineEdit] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class MergeResult:
    plans: list[MergePlan]
    skipped: list[tuple[StepDef, StepDef]]  # pairs touching a step another plan already handles
    changes: dict[Path, tuple[str, str]]  # file -> (before, after), for plans that are ok


# --- placeholders ---------------------------------------------------------

def placeholder_names(step: StepDef) -> list[str] | None:
    """Named placeholders in a pattern, or None if any are unnamed (can't be mapped)."""
    if step.kind == "string":
        return []
    if step.kind == "regex":
        unnamed = re.findall(r"(?<!\\)\((?!\?)", step.pattern)
        return None if unnamed else REGEX_NAMED_GROUP.findall(step.pattern)
    names = [f.partition(":")[0] for f in PARSE_FIELD.findall(step.pattern)]
    return None if "" in names else names


def capture(step: StepDef, text: str) -> dict[str, str] | None:
    """Pull placeholder values out of step text (keyword already stripped)."""
    if step.kind == "regex":
        m = re.fullmatch(step.pattern.removeprefix("^").removesuffix("$"), text)
        return m.groupdict() if m else None
    out, pos = [], 0
    for m in PARSE_FIELD.finditer(step.pattern):
        out.append(re.escape(step.pattern[pos : m.start()]))
        out.append(f"(?P<{m.group(1).partition(':')[0]}>.+?)")  # permissive: also accepts <outline> params
        pos = m.end()
    out.append(re.escape(step.pattern[pos:]))
    m = re.fullmatch("".join(out), text)
    return m.groupdict() if m else None


def render(step: StepDef, values: dict[str, str]) -> str:
    """Write the kept step's wording with the captured values filled in."""
    return PARSE_FIELD.sub(lambda m: values[m.group(1).partition(":")[0]], step.pattern)


# --- source code ----------------------------------------------------------

def _function_node(tree: ast.Module, step: StepDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == step.function
                and any(d.lineno == step.line for d in node.decorator_list)):
            return node
    return None


def _signature_and_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ast.dump(node.args) + ast.dump(ast.Module(body=node.body, type_ignores=[]))


def _decorator_options(node: ast.FunctionDef | ast.AsyncFunctionDef, step: StepDef) -> dict[str, str]:
    """Everything a step decorator says besides its wording, e.g. target_fixture or converters."""
    deco = next(d for d in node.decorator_list if d.lineno == step.line)
    assert isinstance(deco, ast.Call)  # the indexer only records called decorators
    options = {kw.arg or "**kwargs": ast.unparse(kw.value) for kw in deco.keywords}
    options.update({f"argument {i}": ast.unparse(a) for i, a in enumerate(deco.args[1:], start=2)})
    if isinstance(deco.args[0], ast.Call):  # parsers.parse("...", extra_types=...)
        options.update({f"{kw.arg or '**kwargs'} (in the parser)": ast.unparse(kw.value)
                        for kw in deco.args[0].keywords})
    return options


def _references(root: Path, name: str) -> list[str]:
    """Places in the project's Python code that mention a function by name."""
    found = []
    for path in iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and \
                    (node.id if isinstance(node, ast.Name) else node.attr) == name:
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


def _remove_function(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    lines = source.splitlines(keepends=True)
    start = min(d.lineno for d in node.decorator_list) - 1
    end = node.end_lineno or node.lineno
    del lines[start:end]
    # don't leave a double gap where the function was
    while start < len(lines) and not lines[start].strip() and (start == 0 or not lines[start - 1].strip()):
        del lines[start]
    while lines and not lines[-1].strip():
        lines.pop()
    return "".join(lines)


# --- planning -------------------------------------------------------------

def _usage(feature_steps: list[FeatureStep], steps: list[StepDef]) -> Counter[StepDef]:
    return Counter(s for fs in feature_steps for s in steps if binds(fs, s))


def _check_code(plan: MergePlan, root: Path) -> None:
    keep_path, drop_path = root / plan.keep.file, root / plan.drop.file
    keep_node = _function_node(ast.parse(keep_path.read_text(encoding="utf-8")), plan.keep)
    drop_node = _function_node(ast.parse(drop_path.read_text(encoding="utf-8")), plan.drop)
    if keep_node is None or drop_node is None:
        plan.problems.append("couldn't find both functions in the source; re-run index")
        return
    keep_options, drop_options = _decorator_options(keep_node, plan.keep), _decorator_options(drop_node, plan.drop)
    differing = [f"{k}: {keep_options.get(k, 'not set')} vs {drop_options.get(k, 'not set')}"
                 for k in sorted(keep_options.keys() | drop_options.keys())
                 if keep_options.get(k) != drop_options.get(k)]
    if differing:
        plan.problems.append(f"their decorators differ ({'; '.join(differing)})")
    if _signature_and_body(keep_node) != _signature_and_body(drop_node):
        plan.problems.append(f"{plan.keep.function} and {plan.drop.function} don't do the same thing "
                             "(their parameters or code differ)")
    if len(drop_node.decorator_list) > 1:
        plan.problems.append(f"{plan.drop.function} has other decorators; remove it by hand")
    callers = _references(root, plan.drop.function)
    if callers:
        plan.problems.append(f"{plan.drop.function} is used elsewhere: {', '.join(callers)}")


def _plan_edits(plan: MergePlan, feature_steps: list[FeatureStep]) -> None:
    for fs in feature_steps:
        if not binds(fs, plan.drop):
            continue
        path = Path(fs.file)
        line = STEP_LINE.match(fs.raw)
        if line is None:
            continue
        word, raw_text = line.groups()
        values = capture(plan.drop, raw_text.strip())
        if values is None:
            plan.problems.append(f"{path}:{fs.line}: couldn't read the values in this line")
            continue
        new_text = render(plan.keep, values)
        filled = render(plan.keep, capture(plan.drop, fs.text) or values)
        if not is_exact_match(filled, plan.keep):
            plan.problems.append(f"{path}:{fs.line}: rewritten line wouldn't bind to the kept step")
            continue
        old = path.read_text(encoding="utf-8").splitlines()[fs.line - 1]
        indent = old[: len(old) - len(old.lstrip())]
        plan.edits.append(LineEdit(path, fs.line, old, f"{indent}{word} {new_text}"))


def plan_merges(root: Path, threshold: float = 0.6, keep: frozenset[str] = frozenset(),
                features: Path | None = None) -> MergeResult:
    """Work out every merge without writing anything.

    ``root`` is scanned for step definitions and, unless ``features`` names
    another folder, for .feature files too. ``keep`` names functions to prefer as
    the kept step; otherwise the step used in more feature lines wins, and on a
    tie the one defined first.
    """
    root = root.resolve()
    steps = build_index(root)
    feature_steps = [fs for f in iter_feature_files((features or root).resolve()) for fs in parse_feature(f)]
    usage = _usage(feature_steps, steps)
    order = {s: i for i, s in enumerate(steps)}

    plans: list[MergePlan] = []
    skipped: list[tuple[StepDef, StepDef]] = []
    taken: set[StepDef] = set()
    for pair in find_duplicates(steps, threshold):
        if pair.first in taken or pair.second in taken:
            skipped.append((pair.first, pair.second))
            continue
        taken.update((pair.first, pair.second))
        a, b = sorted((pair.first, pair.second),
                      key=lambda s: (s.function not in keep, -usage[s], order[s]))
        plan = MergePlan(keep=a, drop=b, score=pair.score, keep_uses=usage[a], drop_uses=usage[b])
        plans.append(plan)

        if a.keyword != b.keyword:
            plan.problems.append(
                f"one is a {a.keyword.capitalize()} step and the other a {b.keyword.capitalize()} step")
            continue
        names_a, names_b = placeholder_names(a), placeholder_names(b)
        if names_a is None or names_b is None or sorted(names_a) != sorted(names_b):
            plan.problems.append("their placeholders don't line up, so values can't be carried across")
            continue
        if a.kind == "regex" and usage[b]:
            plan.problems.append("feature lines can't be rewritten into a regex step automatically")
            continue
        _check_code(plan, root)
        if plan.ok:
            _plan_edits(plan, feature_steps)

    return MergeResult(plans, skipped, file_changes([p for p in plans if p.ok], root))


def file_changes(plans: list[MergePlan], root: Path) -> dict[Path, tuple[str, str]]:
    """File contents (before, after) for carrying out these plans; ``root`` is the steps folder."""
    before: dict[Path, str] = {}
    after: dict[Path, str] = {}

    def load(path: Path) -> None:
        if path not in before:
            before[path] = after[path] = path.read_text(encoding="utf-8")

    for plan in plans:
        for edit in plan.edits:
            load(edit.file)
            lines = after[edit.file].splitlines(keepends=True)
            ending = lines[edit.line - 1][len(edit.old):]
            lines[edit.line - 1] = edit.new + ending
            after[edit.file] = "".join(lines)

    # delete functions bottom-up so earlier line numbers stay valid
    for plan in sorted(plans, key=lambda p: -p.drop.line):
        path = root / plan.drop.file
        load(path)
        node = _function_node(ast.parse(after[path]), plan.drop)
        if node is not None:
            after[path] = _remove_function(after[path], node)
    return {p: (before[p], after[p]) for p in before if before[p] != after[p]}


def apply(result: MergeResult) -> list[Path]:
    for path, (_, new) in result.changes.items():
        path.write_text(new, encoding="utf-8")
    return list(result.changes)
