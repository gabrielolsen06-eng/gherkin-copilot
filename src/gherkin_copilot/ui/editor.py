"""Scenario editor: list scenarios, open one as blocks, and plan new / edit / delete / duplicate / restore.

A scenario arrives from the browser as a *spec* (kind, name, tags, description,
steps with any data table or doc string, Examples tables). It is validated
here and written with ``gherkin.render``, so the browser never writes raw text
into a file. Every plan is a ``builder.FileChange``: previewed as a diff, saved
only on confirm, refused if the file changed in between.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gherkin_copilot import gherkin
from gherkin_copilot.checker import check_steps, iter_feature_files, parse_feature
from gherkin_copilot.indexer import StepDef, build_index
from gherkin_copilot.merger import capture
from gherkin_copilot.ui.builder import FILE_NAME, FileChange, clean_lines, json_object, list_of, one_line, path_inside
from gherkin_copilot.ui.catalog import segments
from gherkin_copilot.ui.projects import Project

KINDS = {"Scenario", "Example", "Scenario Outline", "Scenario Template", "Background"}
TAG = re.compile(r"^@[^\s@]+$")
NOT_DESCRIPTION = re.compile(
    r"^(Given|When|Then|And|But|\*|Examples|Scenarios|Scenario|Example|Background|Rule|Feature)\b|^[|@#]|^\"\"\"|^```")
MAX_ROWS = 500


# --- validating what the browser sends ------------------------------------------

def _texts(value: object, what: str) -> list[str]:
    texts = list_of(value, str)
    if texts is None:
        raise ValueError(f"{what} must be a list of text.")
    return texts


def _tags(value: object) -> list[str]:
    tags = (value if isinstance(value, str) else " ".join(_texts(value, "Tags"))).split()
    tags = [t if t.startswith("@") else f"@{t}" for t in tags]
    bad = [t for t in tags if not TAG.match(t)]
    if bad:
        raise ValueError(f"Tags are single words starting with @: {', '.join(bad)}")
    return list(dict.fromkeys(tags))


def _description(value: object) -> list[str]:
    lines = [" ".join(line.split()) for line in (value or "").splitlines()] if isinstance(value, str) else None
    if lines is None:
        raise ValueError("The description must be text.")
    lines = [line for line in lines if line]
    for line in lines:
        if NOT_DESCRIPTION.match(line):
            raise ValueError(f"A description line can't start like a step, tag, table or keyword: {line}")
    return lines


def _attachment(value: object) -> list[str]:
    lines = _texts(value or [], "Step attachments")
    if not lines:
        return []
    stripped = [line.strip() for line in lines]
    is_table = all(s.startswith("|") for s in stripped)
    is_doc = len(lines) >= 2 and stripped[0][:3] in ('"""', "```") and stripped[-1].startswith(stripped[0][:3])
    if not (is_table or is_doc) or any("\n" in line for line in lines):
        raise ValueError("A step can only carry a data table or a doc string.")
    return [line.rstrip() for line in lines]


def _comments(value: object) -> list[str]:
    lines = [line.strip() for line in _texts(value or [], "Step comments")]
    if any(not line.startswith("#") or "\n" in line or "\r" in line or len(line) > 500 for line in lines):
        raise ValueError("A step's comments must be single lines starting with #.")
    return lines


def _examples(value: object) -> list[gherkin.Examples]:
    tables = list_of(value, object)
    if tables is None:
        raise ValueError("Examples must be a list of tables.")
    out: list[gherkin.Examples] = []
    for item in tables:
        table = json_object(item)
        if table is None:
            raise ValueError("Each Examples table must be an object.")
        header = [" ".join(h.split()) for h in _texts(table.get("header"), "Column names")]
        if not header or not all(header) or len(set(header)) != len(header):
            raise ValueError("Every Examples column needs a different, non-empty name.")
        rows = list_of(table.get("rows"), object)
        if rows is None or len(rows) > MAX_ROWS:
            raise ValueError(f"An Examples table can have up to {MAX_ROWS} rows.")
        cells = [[" ".join(c.split()) for c in _texts(r, "Rows")][: len(header)] for r in rows]
        out.append(gherkin.Examples("Examples", " ".join(str(table.get("name") or "").split())[:200],
                                    _tags(table.get("tags") or []), header, cells))
    return out


def clean_spec(spec: object, indent: str = "  ") -> gherkin.Block:
    data = json_object(spec)
    if data is None:
        raise ValueError("Send the scenario as an object.")
    kind = data.get("kind") or "Scenario"
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Unknown kind of scenario.")
    name = " ".join(str(data.get("name") or "").split())
    if kind != "Background":
        name = one_line(name, "scenario")
    step_data: list[dict[str, object]] | None = list_of(data.get("steps"), dict)
    if step_data is None:
        raise ValueError("Steps must be a list.")
    lines = clean_lines([s.get("line") for s in step_data])
    if not lines:
        raise ValueError("Add at least one step before saving.")
    steps: list[gherkin.Step] = []
    for line, raw in zip(lines, step_data, strict=True):
        word, text = line.split(None, 1)
        steps.append(gherkin.Step(word, text, 0, _attachment(raw.get("attachment")), _comments(raw.get("comments"))))
    examples = _examples(data.get("examples") or [])
    outline = kind in gherkin.OUTLINES
    if outline and not examples:
        raise ValueError("A Scenario Outline needs an Examples table.")
    if not outline and examples:
        raise ValueError("Only a Scenario Outline can have Examples.")
    tags = _tags(data.get("tags") or []) if kind != "Background" else []
    return gherkin.Block(kind, name, tags, _description(data.get("description") or ""), steps, examples, 0, 0, indent)


# --- reading ------------------------------------------------------------------------

def _feature_path(project: Project, file: str) -> Path:
    path = path_inside(Path(project.features), file, ".feature")
    if not path.is_file():
        raise ValueError("That feature file no longer exists.")
    return path


def list_scenarios(project: Project) -> dict[str, Any]:
    folder = Path(project.features)
    index = build_index(Path(project.steps))
    files: list[dict[str, Any]] = []
    for path in iter_feature_files(folder):
        text = path.read_text(encoding="utf-8")
        status = {r.step.line: r.status for r in check_steps(parse_feature(path), index, suggest=False)}
        feature = gherkin.parse(text)
        blocks: list[dict[str, Any]] = []
        for b in feature.blocks:
            states = [status.get(s.line, "missing") for s in b.steps]
            blocks.append({"kind": b.kind, "name": b.name, "tags": b.tags, "start": b.start, "end": b.end,
                           "steps": len(b.steps), "missing": states.count("missing"),
                           "ambiguous": states.count("ambiguous"),
                           "examples": sum(len(e.rows) for e in b.examples)})
        files.append({"file": path.relative_to(folder).as_posix(), "feature": feature.name, "tags": feature.tags,
                      "description": feature.description, "blocks": blocks})
    return {"files": files}


def _find_block(feature: gherkin.FeatureFile, start: object, name: object) -> gherkin.Block:
    block = next((b for b in feature.blocks if b.start == start and b.name == name), None)
    if block is None:
        raise ValueError("This scenario has moved or changed in the file since you opened it. Open it again.")
    return block


def _keepable(text: str, block: gherkin.Block) -> gherkin.Block:
    """Refuse a scenario with lines the editor can't write back, rather than drop them on save."""
    if block.unkept:
        line = block.unkept[0]
        raise ValueError(f"Line {line} (“{text.splitlines()[line - 1].strip()[:80]}”) would be lost "
                         "if the app saved this scenario: it isn't a step, a table under a step, or a comment just "
                         "above a step. Fix or move it in your text editor, then open the scenario again.")
    return block


def _step_values(step: StepDef, text: str) -> dict[str, str] | None:
    try:
        return capture(step, text)
    except re.error:  # e.g. unnamed {} placeholders
        return None


def open_scenario(project: Project, file: str, start: object, name: object) -> dict[str, Any]:
    path = _feature_path(project, file)
    text = path.read_text(encoding="utf-8")
    feature = gherkin.parse(text)
    block = _keepable(text, _find_block(feature, start, name))
    index = build_index(Path(project.steps))
    results = {r.step.line: r for r in check_steps(parse_feature(path), index, suggest=False)}
    steps: list[dict[str, Any]] = []
    for s in block.steps:
        result = results.get(s.line)
        base = result.step.keyword if result else "given"
        match = result.bound[0] if result and result.status == "ok" else None
        values = _step_values(match, s.text) if match else None
        steps.append({
            "keyword": base,
            "written": s.keyword,
            "text": s.text,
            "attachment": s.attachment,
            "comments": s.comments,
            "step": ({**asdict(match), "segments": segments(match)}
                     if match is not None and values is not None else None),
            "values": values or {},
        })
    return {
        "file": file, "start": block.start, "original_name": block.name, "kind": block.kind, "name": block.name,
        "tags": block.tags, "description": "\n".join(block.description), "steps": steps,
        "examples": [{"name": e.name, "tags": e.tags, "header": e.header, "rows": e.rows} for e in block.examples],
        "feature": feature.name,
    }


def outline_check_text(lines: list[str], examples: object) -> str:
    """Draft text for checking; an outline's placeholders are filled from its first Examples row."""
    tables = _examples(examples) if examples else []
    body = "".join(f"    {line}\n" for line in lines)
    if not tables or not tables[0].header:
        return f"Feature: draft\n  Scenario: draft\n{body}"
    first = tables[0]
    row = first.rows[0] if first.rows else [f"x{i}" for i in range(len(first.header))]
    table = "\n".join(gherkin.render_table(first.header, [row], "      "))
    return f"Feature: draft\n  Scenario Outline: draft\n{body}\n    Examples:\n{table}\n"


# --- planning changes -----------------------------------------------------------------

def _scenario_text(text: str, block: gherkin.Block) -> str:
    return "".join(text.splitlines(keepends=True)[block.start - 1 : block.end])


def plan_new(project: Project, mode: str, file: str, feature_name: str, spec: object) -> tuple[FileChange, str]:
    """Create a new .feature file with this scenario, or add it to the end of an existing one."""
    folder = Path(project.features)
    if mode == "new":
        name = file.strip().removesuffix(".feature")
        if not FILE_NAME.match(name):
            raise ValueError("Use a file name made of letters, numbers, spaces, - or _.")
        path = folder / f"{name}.feature"
        if path.exists():
            raise ValueError(f"{path.name} already exists. Choose “Add to an existing file” instead.")
        block = clean_spec(spec)
        rendered = gherkin.render(block)
        return FileChange(path, path.name, "", f"Feature: {one_line(feature_name, 'feature')}\n\n{rendered}",
                          creates=True), rendered
    if mode == "append":
        path = _feature_path(project, file)
        before = path.read_text(encoding="utf-8")
        existing = gherkin.parse(before)
        indent = existing.blocks[-1].indent if existing.blocks else "  "
        block = clean_spec(spec, indent)
        if block.kind == "Background" and existing.background:
            raise ValueError("That file already has a Background.")
        rendered = gherkin.render(block)
        after = before.rstrip("\n") + "\n\n" + rendered
        return FileChange(path, path.relative_to(folder).as_posix(), before, after, creates=False), rendered
    raise ValueError("Choose a new file or an existing one.")


def _existing(project: Project, file: str, start: object,
              name: object) -> tuple[Path, str, gherkin.FeatureFile, gherkin.Block]:
    path = _feature_path(project, file)
    text = path.read_text(encoding="utf-8")
    feature = gherkin.parse(text)
    return path, text, feature, _find_block(feature, start, name)


def _change(project: Project, path: Path, before: str, after: str) -> FileChange:
    return FileChange(path, path.relative_to(Path(project.features)).as_posix(), before, after, creates=False)


def plan_edit(project: Project, file: str, start: object, name: object, spec: object) -> tuple[FileChange, str, str]:
    path, text, _, block = _existing(project, file, start, name)
    _keepable(text, block)
    new = clean_spec(spec, block.indent)
    if (new.kind == "Background") != (block.kind == "Background"):
        raise ValueError("A Background can't become a scenario, or the other way round.")
    rendered = gherkin.render(new)
    after = gherkin.replace_span(text, block.start, block.end, rendered.rstrip("\n") + "\n")
    return _change(project, path, text, after), _scenario_text(text, block), rendered


def plan_delete(project: Project, file: str, start: object, name: object) -> tuple[FileChange, str]:
    path, text, _, block = _existing(project, file, start, name)
    return _change(project, path, text, gherkin.replace_span(text, block.start, block.end, None)), \
        _scenario_text(text, block)


def plan_duplicate(project: Project, file: str, start: object, name: object) -> tuple[FileChange, str]:
    path, text, feature, block = _existing(project, file, start, name)
    if block.kind == "Background":
        raise ValueError("A feature can only have one Background.")
    _keepable(text, block)
    taken = {b.name for b in feature.blocks}
    copy_name, n = f"{block.name} (copy)", 2
    while copy_name in taken:
        copy_name, n = f"{block.name} (copy {n})", n + 1
    copy = gherkin.Block(block.kind, copy_name, block.tags, block.description, block.steps, block.examples,
                         0, 0, block.indent)
    rendered = gherkin.render(copy)
    return _change(project, path, text, gherkin.insert_after(text, block.end, rendered)), rendered


def plan_restore(project: Project, entry: dict[str, Any]) -> tuple[FileChange, str | None, str | None]:
    """Put one scenario back the way it was before a recorded change."""
    before, after, file = entry.get("before"), entry.get("after"), entry.get("file", "")
    if before is None and after is None:
        raise ValueError("Only changes to a single scenario can be restored. Use the diff to undo this one by hand.")
    path = _feature_path(project, file)
    text = path.read_text(encoding="utf-8")
    feature = gherkin.parse(text)
    current = None
    if after is not None:
        wanted = gherkin.parse("Feature: x\n" + after).blocks
        current = next((b for b in feature.blocks if _scenario_text(text, b).strip() == after.strip()), None) or \
            next((b for b in feature.blocks if wanted and b.name == wanted[0].name and b.kind == wanted[0].kind), None)
        if current is None:
            raise ValueError("That scenario isn't in the file any more, so there's nothing to restore it over.")
    if current is None:  # it was deleted: add it back at the end
        if before is None:
            raise ValueError("There's no earlier version of this scenario to restore.")
        new_text = text.rstrip("\n") + "\n\n" + before.rstrip("\n") + "\n"
    elif before is None:  # it was created: restoring the earlier state removes it
        new_text = gherkin.replace_span(text, current.start, current.end, None)
    else:
        new_text = gherkin.replace_span(text, current.start, current.end, before.rstrip("\n") + "\n")
    if new_text == text:
        raise ValueError("The file already looks like that.")
    return _change(project, path, text, new_text), (_scenario_text(text, current) if current else None), before
