"""Turn a Cucumber JSON report into a single, self-contained HTML page.

Works with the JSON written by pytest-bdd (``--cucumberjson``), behave's JSON
formatter, or Cucumber itself. If Playwright artifacts (screenshots, videos,
traces) were saved by pytest-playwright, they are linked to their scenario.

The page has no scripts and loads nothing from the internet: all styling is
inline and every value from the report is HTML-escaped.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

STATUS_ORDER = ["failed", "undefined", "pending", "skipped", "passed"]
ARTIFACT_KINDS = {".png": "screenshots", ".jpg": "screenshots", ".webm": "videos", ".zip": "traces"}


@dataclass
class Step:
    keyword: str
    name: str
    line: int
    status: str
    seconds: float
    error: str


@dataclass
class Scenario:
    name: str
    keyword: str
    test_id: str
    line: int
    tags: list[str]
    steps: list[Step]
    artifacts: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def status(self) -> str:
        statuses = {s.status for s in self.steps} or {"skipped"}
        return next((s for s in STATUS_ORDER if s in statuses), "passed")

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    @property
    def example(self) -> str:
        """The Examples row of an outline, taken from the pytest parameter id."""
        m = re.search(r"\[(.*)\]$", self.test_id)
        return m.group(1) if m else ""


@dataclass
class Feature:
    name: str
    uri: str
    description: str
    scenarios: list[Scenario]

    @property
    def status(self) -> str:
        statuses = {s.status for s in self.scenarios}
        return next((s for s in STATUS_ORDER if s in statuses), "passed")


def _number(value: object) -> float:
    """A number from JSON that might be missing, a string, or junk."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _seconds(duration: object) -> float:
    """Cucumber JSON durations are nanoseconds."""
    return _number(duration) / 1_000_000_000


def _line(value: object) -> int:
    return int(_number(value))


def _step(s: dict[str, Any]) -> Step:
    result = s.get("result") or {}
    return Step(
        keyword=str(s.get("keyword", "")).strip(),
        name=str(s.get("name", "")),
        line=_line(s.get("line")),
        status=str(result.get("status", "skipped")),
        seconds=_seconds(result.get("duration")),
        error=str(result.get("error_message") or ""),
    )


def load_report(path: Path) -> list[Feature]:
    """Read Cucumber JSON. Raises ValueError with a readable message if the file isn't valid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _features(data)
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError) as exc:
        raise ValueError(f"{path} isn't a valid Cucumber JSON report ({exc}).") from exc


def _features(data: list[Any]) -> list[Feature]:
    features = []
    for f in data:
        scenarios = []
        background: list[Step] = []
        for el in f.get("elements", []):
            if el.get("type") == "background":
                # Cucumber-JVM and Cucumber-JS report a feature's Background as its own element
                # before each scenario (pytest-bdd inlines it): keep its steps with that scenario
                background = [_step(s) for s in el.get("steps", [])]
                continue
            steps = background + [_step(s) for s in el.get("steps", [])]
            background = []
            # pytest-bdd marks every step after a failure as failed too; they never ran
            for i, step in enumerate(steps):
                if step.status == "failed" and any(p.status == "failed" for p in steps[:i]) and not step.error:
                    step.status = "skipped"
            scenarios.append(Scenario(
                name=str(el.get("name", "")),
                keyword=str(el.get("keyword", "Scenario")),
                test_id=str(el.get("id", "")),
                line=_line(el.get("line")),
                tags=[str(t.get("name", "")) for t in el.get("tags", [])],
                steps=steps,
            ))
        features.append(Feature(str(f.get("name", "")), str(f.get("uri", "")), str(f.get("description", "")),
                                scenarios))
    return features


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def attach_artifacts(features: list[Feature], artifacts_dir: Path) -> None:
    """Match pytest-playwright's per-test folders (named after the test id) to scenarios."""
    if not artifacts_dir.is_dir():
        return
    folders = [d for d in artifacts_dir.iterdir() if d.is_dir()]
    for scenario in (s for f in features for s in f.scenarios):
        slug = _slug(scenario.test_id)
        if not slug:
            continue
        pattern = re.compile(rf"(^|-){re.escape(slug)}(-(chromium|firefox|webkit|chrome|msedge))?$")
        for folder in folders:
            if pattern.search(folder.name):
                for file in sorted(folder.iterdir()):
                    kind = ARTIFACT_KINDS.get(file.suffix.lower())
                    if kind:
                        scenario.artifacts.setdefault(kind, []).append(file)


def _rel(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start)).as_posix()


def _badge(status: str) -> str:
    return f'<span class="badge {escape(status)}">{escape(status)}</span>'


def _fmt_seconds(value: float) -> str:
    return f"{value:.2f}s" if value < 60 else f"{int(value // 60)}m {value % 60:.0f}s"


def _short_error(error: str) -> str:
    """Pytest errors are long; the lines starting with 'E ' are the useful part."""
    lines = [ln[1:].strip() for ln in error.splitlines() if ln.startswith("E ")]
    return "\n".join(lines) or error.strip()


def _scenario_html(s: Scenario, out_dir: Path) -> str:
    title = escape(s.name) + (f' <span class="example">{escape(s.example)}</span>' if s.example else "")
    tags = "".join(f'<span class="tag">{escape(t)}</span>' for t in s.tags)
    rows = []
    for step in s.steps:
        error = ""
        if step.error:
            error = (f'<pre class="error">{escape(_short_error(step.error))}</pre>'
                     f'<details class="trace"><summary>Full error</summary><pre>{escape(step.error)}</pre></details>')
        rows.append(
            f'<li class="step {escape(step.status)}"><span class="dot"></span>'
            f'<span class="kw">{escape(step.keyword)}</span> {escape(step.name)}'
            f'<span class="dur">{_fmt_seconds(step.seconds)}</span>{error}</li>'
        )

    media = []
    for shot in s.artifacts.get("screenshots", []):
        src = escape(_rel(shot, out_dir))
        media.append(f'<a href="{src}"><img src="{src}" alt="Screenshot: {escape(shot.stem)}"></a>')
    for video in s.artifacts.get("videos", []):
        media.append(f'<video src="{escape(_rel(video, out_dir))}" controls preload="metadata"></video>')
    traces = "".join(
        f'<p class="tracecmd">Step-by-step replay: from the project folder, run '
        f'<code>python -m playwright show-trace --host 127.0.0.1 --port 0 "{escape(_rel(t, Path.cwd()))}"</code>'
        f' (opens in your default browser)</p>'
        for t in s.artifacts.get("traces", [])
    )
    media_html = f'<div class="media">{"".join(media)}</div>' if media else ""

    is_open = " open" if s.status != "passed" else ""
    return (
        f'<details class="scenario {escape(s.status)}"{is_open}>'
        f'<summary>{_badge(s.status)}<span class="sname">{title}</span>{tags}'
        f'<span class="dur">{_fmt_seconds(s.seconds)}</span></summary>'
        f'<ol class="steps">{"".join(rows)}</ol>{media_html}{traces}</details>'
    )


def render_html(features: list[Feature], out_dir: Path, source: str) -> str:
    scenarios = [s for f in features for s in f.scenarios]
    counts = {k: sum(s.status == k for s in scenarios) for k in STATUS_ORDER}
    steps = [st for s in scenarios for st in s.steps]
    overall = "passed" if scenarios and counts["passed"] == len(scenarios) else ("failed" if scenarios else "skipped")
    total_time = sum(s.seconds for s in scenarios)
    pass_rate = (counts["passed"] / len(scenarios) * 100) if scenarios else 0

    tiles = [
        ("Scenarios", str(len(scenarios))),
        ("Passed", str(counts["passed"])),
        ("Failed", str(counts["failed"] + counts["undefined"])),
        ("Steps", str(len(steps))),
        ("Pass rate", f"{pass_rate:.0f}%"),
        ("Duration", _fmt_seconds(total_time)),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tv">{escape(v)}</div><div class="tl">{escape(k)}</div></div>'
                         for k, v in tiles)

    sections = []
    for f in features:
        desc = f'<p class="desc">{escape(f.description)}</p>' if f.description else ""
        sections.append(
            f'<section class="feature"><h2>{_badge(f.status)} {escape(f.name)}'
            f' <span class="uri">{escape(f.uri)}</span></h2>{desc}'
            + "".join(_scenario_html(s, out_dir) for s in f.scenarios) + "</section>"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'">
<title>Test report</title>
<style>{CSS}</style>
</head>
<body>
<header class="top {overall}">
  <h1>Test report</h1>
  <p>{escape(source)} · generated {generated}</p>
</header>
<main>
  <div class="tiles">{tiles_html}</div>
  {"".join(sections) or '<p>No scenarios found in this report.</p>'}
</main>
</body>
</html>
"""


def write_report(json_path: Path, out_path: Path, artifacts_dir: Path | None = None) -> list[Feature]:
    features = load_report(json_path)
    if artifacts_dir:
        attach_artifacts(features, artifacts_dir)
    out_path.write_text(render_html(features, out_path.resolve().parent, json_path.name), encoding="utf-8")
    return features


CSS = """
:root { --ok:#1f9d55; --bad:#d64545; --warn:#c98a00; --muted:#66717a; --line:#e3e7ea; --bg:#f6f8f9; --card:#fff;
  --ink:#1b2328;
  color-scheme: light dark; font-family: system-ui, -apple-system, sans-serif; color: var(--ink); }
@media (prefers-color-scheme: dark) { :root { --line:#2c353b; --bg:#12171a; --card:#1a2126; --ink:#e6ebee;
  --muted:#93a0a8; } }
body { margin:0; background:var(--bg); }
.top { padding:20px 24px; color:#fff; background:var(--ok); }
.top.failed { background:var(--bad); }
.top.skipped { background:var(--muted); }
.top h1 { margin:0; font-size:1.4rem; }
.top p { margin:4px 0 0; opacity:.9; font-size:.9rem; }
main { max-width:1000px; margin:0 auto; padding:20px 16px 60px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:24px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.tv { font-size:1.5rem; font-weight:700; font-variant-numeric:tabular-nums; }
.tl { color:var(--muted); font-size:.8rem; }
.feature h2 { font-size:1.1rem; display:flex; gap:8px; align-items:center; margin:28px 0 6px; }
.uri { color:var(--muted); font-weight:400; font-size:.8rem; }
.desc { color:var(--muted); margin:0 0 10px; }
.scenario { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--ok); border-radius:8px;
  margin:8px 0; }
.scenario.failed, .scenario.undefined { border-left-color:var(--bad); }
.scenario.skipped, .scenario.pending { border-left-color:var(--warn); }
summary { cursor:pointer; padding:10px 12px; display:flex; gap:8px; align-items:center; }
.sname { flex:1; font-weight:600; }
.example { color:var(--muted); font-weight:400; font-size:.85rem; }
.dur { color:var(--muted); font-size:.8rem; font-variant-numeric:tabular-nums; margin-left:auto; }
.badge { font-size:.7rem; text-transform:uppercase; letter-spacing:.04em; padding:2px 7px; border-radius:999px;
  color:#fff; background:var(--ok); }
.badge.failed, .badge.undefined { background:var(--bad); }
.badge.skipped, .badge.pending { background:var(--warn); }
.tag { font-size:.75rem; color:var(--muted); border:1px solid var(--line); border-radius:4px; padding:0 5px; }
.steps { list-style:none; margin:0; padding:0 12px 10px 14px; }
.step { display:flex; flex-wrap:wrap; gap:6px; align-items:baseline; padding:5px 0; border-top:1px solid var(--line);
  }
.dot { width:8px; height:8px; border-radius:50%; background:var(--ok); flex:none; align-self:center; }
.step.failed .dot, .step.undefined .dot { background:var(--bad); }
.step.skipped .dot, .step.pending .dot { background:var(--warn); }
.kw { font-weight:700; color:var(--muted); }
.error { flex-basis:100%; margin:4px 0 0; padding:8px 10px; background:rgba(214,69,69,.1); color:var(--bad);
  border-radius:6px; white-space:pre-wrap; font-size:.85rem; }
details.trace { flex-basis:100%; }
details.trace summary { padding:4px 0; font-size:.8rem; color:var(--muted); }
details.trace pre { max-height:300px; overflow:auto; font-size:.75rem; background:var(--bg); padding:8px;
  border-radius:6px; }
.media { display:flex; flex-wrap:wrap; gap:10px; padding:0 12px 12px; }
.media img, .media video { max-width:100%; width:440px; border:1px solid var(--line); border-radius:6px; }
.tracecmd { padding:0 12px 12px; margin:0; font-size:.8rem; color:var(--muted); }
code { font-size:.75rem; word-break:break-all; background:var(--bg); padding:1px 4px; border-radius:4px; }
"""
