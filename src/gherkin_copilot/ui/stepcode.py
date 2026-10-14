"""Action blocks: build the code of a new step from a list of actions, without writing Python.

The browser sends the step's wording and a list of actions as data, for example

    {"action": "click", "target": {"by": "button", "value": "Checkout"}}
    {"action": "fill", "target": {"by": "label", "value": "First Name"}, "value": {"param": "first_name"}}

and this module writes the Playwright code. Only the actions and element types
listed here exist; every text the user typed ends up inside a Python string
literal (via ``repr``), and step values can only be the step's own parameters,
so nothing from the browser is ever pasted in as code. The result is compiled
and checked to match the scenario line before it can be saved.
"""

from __future__ import annotations

import ast
import keyword as py_keyword
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gherkin_copilot.checker import FeatureStep, stub_parts
from gherkin_copilot.indexer import StepDef, index_file, uses_desktop_helper
from gherkin_copilot.matcher import is_exact_match
from gherkin_copilot.ui.builder import STEP_START, FileChange, json_object, list_of, module_names, path_inside
from gherkin_copilot.ui.projects import Project

# element types -> how Playwright finds them
TARGETS = {
    "button": ("Button with text", 'page.get_by_role("button", name={v})'),
    "link": ("Link with text", 'page.get_by_role("link", name={v})'),
    "label": ("Field labelled", "page.get_by_label({v})"),
    "placeholder": ("Field with placeholder", "page.get_by_placeholder({v})"),
    "text": ("Text on the page", "page.get_by_text({v})"),
    "testid": ("Test id", "page.get_by_test_id({v})"),
    "css": ("CSS selector", "page.locator({v})"),
}

# action -> (label, needs an element, needs a value, code template)
ACTIONS = {
    "goto": ("Go to page", False, True, "page.goto({value})"),
    "click": ("Click", True, False, "{target}.click()"),
    "fill": ("Type into", True, True, "{target}.fill({value})"),
    "select": ("Choose option", True, True, "{target}.select_option({value})"),
    "check": ("Tick checkbox", True, False, "{target}.check()"),
    "uncheck": ("Untick checkbox", True, False, "{target}.uncheck()"),
    "press": ("Press key", True, True, "{target}.press({value})"),
    "expect_text": ("Check it shows text", True, True, "expect({target}).to_contain_text({value})"),
    "expect_visible": ("Check it's visible", True, False, "expect({target}).to_be_visible()"),
    "expect_hidden": ("Check it's hidden", True, False, "expect({target}).to_be_hidden()"),
    "expect_count": ("Check how many there are", True, True, "expect({target}).to_have_count({count})"),
    "expect_url": ("Check the address contains", False, True,
                   "expect(page).to_have_url(re.compile(re.escape({value})))"),
    "wait": ("Wait for it to appear", True, False, "{target}.wait_for()"),
}
# Desktop apps (projects whose steps import uia, the helper `init --desktop` creates): a control is
# found by the name Narrator reads out, or its AutomationId, and the helper does the rest
DESKTOP_TARGETS = {"name": ("Control called", "{v}")}
DESKTOP_ACTIONS = {
    "click": ("Click", True, False, "uia.click(app, {target})"),
    "tick": ("Tick", True, False, "uia.tick(app, {target})"),
    "fill": ("Type into", True, True, "uia.type_into(app, {target}, {value})"),
    "expect_text": ("Check it shows", True, True, "uia.expect_text(app, {target}, {value})"),
    "expect_ticked": ("Check it's ticked", True, False, "uia.expect_ticked(app, {target})"),
    "expect_visible": ("Check text appears", False, True, "uia.expect_visible(app, {value})"),
}
KINDS = {"web": (ACTIONS, TARGETS, "page"), "desktop": (DESKTOP_ACTIONS, DESKTOP_TARGETS, "app")}
MAX_ACTIONS = 50
MAX_TEXT = 500
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
NEW_FILE = re.compile(r"^[a-z0-9_]{1,60}\.py$")


def project_kind(project: Project) -> str:
    """"desktop" if the project's steps drive a Windows app through the uia helper, otherwise "web"."""
    return "desktop" if uses_desktop_helper(Path(project.steps)) else "web"


def catalogue(kind: str = "web") -> dict[str, Any]:
    """What the editor can offer for this kind of project."""
    actions, targets, _ = KINDS[kind]
    return {
        "kind": kind,
        "actions": [{"id": k, "label": v[0], "target": v[1], "value": v[2]} for k, v in actions.items()],
        "targets": [{"id": k, "label": v[0]} for k, v in targets.items()],
    }


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Fill in {what}.")
    if len(value) > MAX_TEXT or "\n" in value:
        raise ValueError(f"Keep {what} to one line under {MAX_TEXT} characters.")
    return value


def draft(line: str, keyword: str) -> dict[str, Any]:
    """The new step's wording, values and function name, worked out from a scenario line."""
    if keyword not in {"given", "when", "then"}:
        raise ValueError("Unknown keyword.")
    match = STEP_START.match(line or "")
    if not match:
        raise ValueError("That isn't a step line.")
    text = line.split(None, 1)[1]
    pattern, params, function = stub_parts(FeatureStep(keyword, text, line, "", 0))
    return {"keyword": keyword, "text": text, "pattern": pattern, "function": function,
            "params": [{"name": name, "type": "number" if fmt else "text"} for name, fmt in params]}


def _renamed(info: dict[str, Any], names: object, fixture: str = "page") -> tuple[str, list[tuple[str, str]]]:
    """Apply the user's names for the step's values: {text1} -> {first_name}."""
    chosen = json_object(names or {})
    if chosen is None:
        raise ValueError("Value names must be an object.")
    pattern: str = info["pattern"]
    params: list[tuple[str, str]] = []
    for p in info["params"]:
        new = chosen.get(p["name"], p["name"])
        if not isinstance(new, str) or not IDENTIFIER.match(new) or py_keyword.iskeyword(new) or new == fixture:
            raise ValueError(f"“{new}” can't be a value name: use lowercase letters, numbers and _ only.")
        if new in (n for n, _ in params):
            raise ValueError(f"Two values are both called “{new}”.")
        fmt = ":d" if p["type"] == "number" and "{" + p["name"] + ":d}" in pattern else \
            ":g" if p["type"] == "number" else ""
        pattern = pattern.replace("{" + p["name"] + fmt + "}", "{" + new + fmt + "}", 1)
        params.append((new, p["type"]))
    return pattern, params


def _value(value: object, params: list[tuple[str, str]], what: str) -> str:
    data = json_object(value)
    if data is not None and "param" in data:
        name = data["param"]
        kinds = dict(params)
        if not isinstance(name, str) or name not in kinds:
            raise ValueError(f"{what}: “{name}” isn't one of this step's values.")
        return f"str({name})" if kinds[name] == "number" else name
    if data is not None:
        value = data.get("text")
    return repr(_text(value, what))


def code(line: str, keyword: str, names: object, actions: object, kind: str = "web") -> dict[str, Any]:
    """The full step definition for these actions. Raises ValueError with a readable message."""
    action_table, target_table, fixture = KINDS[kind]
    info = draft(line, keyword)
    pattern, params = _renamed(info, names, fixture)
    steps = list_of(actions, object)
    if not steps:
        raise ValueError("Add at least one action.")
    if len(steps) > MAX_ACTIONS:
        raise ValueError(f"A step can have up to {MAX_ACTIONS} actions.")
    body: list[str] = []
    for n, raw in enumerate(steps, start=1):
        item = json_object(raw) or {}
        action = item.get("action")
        if not isinstance(action, str) or action not in action_table:
            raise ValueError(f"Action {n}: choose what it does.")
        label, needs_target, needs_value, template = action_table[action]
        parts: dict[str, str] = {}
        if needs_target:
            target = json_object(item.get("target")) or {}
            by = target.get("by")
            if not isinstance(by, str) or by not in target_table:
                raise ValueError(f"Action {n} ({label}): choose how to find the element.")
            element = _text(target.get("value"), f"action {n}'s element")
            parts["target"] = target_table[by][1].format(v=repr(element))
        if needs_value:
            parts["value"] = _value(item.get("value"), params, f"Action {n} ({label})")
            # a count needs a number: a number value is one already, anything else is converted
            number_param = parts["value"].startswith("str(")
            parts["count"] = parts["value"][4:-1] if number_param else f"int({parts['value']})"
        body.append("    " + template.format(**parts))

    args = ", ".join([fixture, *(name for name, _ in params)])
    if not params:  # a plain string step: no {placeholders}, so braces are literal
        pattern = pattern.replace("{{", "{").replace("}}", "}")
    decorator = f"@{keyword}(parsers.parse({pattern!r}))" if params else f"@{keyword}({pattern!r})"
    source = f"{decorator}\ndef {info['function']}({args}):\n" + "\n".join(body) + "\n"
    compile(source, "<step>", "exec")  # always valid Python; a failure here is a bug, not user error

    step = StepDef(keyword, pattern, "parse" if params else "string", info["function"], "", 0)
    if not is_exact_match(info["text"], step):
        raise ValueError("With these value names the step wouldn't match the scenario line any more.")
    return {"code": source, "pattern": pattern, "function": info["function"]}


def _imports_needed(source: str, existing: set[str]) -> list[str]:
    lines: list[str] = []
    bdd = sorted({kw for kw in ("given", "when", "then") if f"@{kw}(" in source} |
                 ({"parsers"} if "parsers." in source else set()))
    missing_bdd = [n for n in bdd if n not in existing]
    if missing_bdd:
        lines.append(f"from pytest_bdd import {', '.join(missing_bdd)}")
    if "expect(" in source and "expect" not in existing:
        lines.append("from playwright.sync_api import expect")
    if "re.compile(" in source and "re" not in existing:
        lines.append("import re")
    if "uia." in source and "uia" not in existing:
        lines.append("import uia")
    return lines


def plan(project: Project, line: str, keyword: str, names: object, actions: object, file: str,
         new_file: bool, already_exists: Callable[[str], bool]) -> FileChange:
    """Where the code goes: appended to an existing step file, or a new one in the steps folder."""
    if already_exists(line):
        raise ValueError("This step already exists. Remove the line's text block and use the existing step instead.")
    generated = code(line, keyword, names, actions, project_kind(project))
    source = generated["code"]
    folder = Path(project.steps)
    if new_file:
        relative = file.strip().replace("\\", "/")
        parent, _, name = relative.rpartition("/")
        if not NEW_FILE.match(name):
            raise ValueError("Name the new file with lowercase letters, numbers and _, "
                             "ending in .py (e.g. checkout_steps.py).")
        path = path_inside(folder, relative, ".py") if parent else folder / name
        if not path.parent.is_dir():
            raise ValueError("That folder doesn't exist in the steps folder.")
        if path.exists():
            raise ValueError(f"{relative} already exists. Choose it from the list instead.")
        header = "\n".join(_imports_needed(source, set()))
        after = f'"""Steps built with gherkin-copilot\'s action blocks."""\n\n{header}\n\n\n{source}'
        return FileChange(path, path.relative_to(folder).as_posix(), "", after, creates=True)

    path = path_inside(folder, file, ".py")
    if not path.is_file() or not index_file(path):
        raise ValueError("Pick one of the project's step files.")
    before = path.read_text(encoding="utf-8")
    tree = ast.parse(before.removeprefix("\ufeff"))  # a BOM some Windows editors add
    existing = module_names(tree)
    function, new, n = generated["function"], generated["function"], 2
    while new in existing:
        new, n = f"{function}_{n}", n + 1
    source = source.replace(f"def {function}(", f"def {new}(", 1)
    out = before.rstrip("\n").splitlines()
    imports = _imports_needed(source, existing)
    if imports:
        nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        at = nodes[-1].end_lineno if nodes else 0
        out[at:at] = imports
    after = "\n".join(out) + "\n\n\n" + source
    return FileChange(path, path.relative_to(folder).as_posix(), before, after, creates=False)
