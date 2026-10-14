"""Run a project's tests from the app and read the results.

This is the only part of gherkin-copilot that starts another program, and it
only ever runs the test command the user saved in the project's settings:
split into an argument list (never passed to a shell), started from the
project's chosen folder, one run per project at a time, stopped on request or
after a time limit. The app adds its own pytest options so results land in
the project's ``.gherkin-copilot/results`` folder, which ignores itself in git.

To run one scenario or one feature file, the tests are collected first and
picked by name with ``-k``: pytest-bdd names each test ``test_`` + the
scenario name.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess  # nosec B404: runs only the user's own saved pytest command, see SECURITY.md
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gherkin_copilot import gherkin
from gherkin_copilot.report import ARTIFACT_KINDS, attach_artifacts, load_report, short_error
from gherkin_copilot.ui.builder import path_inside
from gherkin_copilot.ui.history import project_root
from gherkin_copilot.ui.projects import Project

ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
TIME_LIMIT = 30 * 60
LOG_LINES = 3000
RESULT_LINE = re.compile(r"^\S+::\S.*\s(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(\s|$)")  # "t.py::test_a PASSED [50%]"


def split_command(text: str, windows: bool = os.name == "nt") -> list[str]:
    """Split like a shell: quotes group words and are removed. On Windows a backslash is a path separator,
    not an escape (shlex's non-POSIX mode would keep the quotes, so pytest would get -k '"a b"')."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if windows:
        lexer.escape = ""
    return list(lexer)


def parse_command(text: str) -> tuple[dict[str, str], list[str]]:
    """``DEMO_BROWSER=brave python -m pytest demo`` -> ({"DEMO_BROWSER": "brave"}, [python, -m, pytest, demo])."""
    try:
        tokens = split_command(text)
    except ValueError as exc:
        raise ValueError(f"The test command can't be read: {exc}") from exc
    env: dict[str, str] = {}
    while tokens and ENV_ASSIGNMENT.match(tokens[0]):
        key, _, value = tokens.pop(0).partition("=")
        env[key] = value
    if not tokens:
        raise ValueError("Enter a test command, for example: python -m pytest tests")
    if tokens[0] in {"source", "cd", "export", "set"} or {"&&", "||", ";", "|", "&"} & set(tokens):
        raise ValueError("Enter a single command, without source, cd, export or &&. The app already uses the "
                         "Python it was started with, and the folder goes in “Run from folder”.")
    is_pytest = Path(tokens[0]).stem.startswith("pytest") or tokens[1:3] == ["-m", "pytest"]
    if not is_pytest:
        if len(tokens) > 1 and tokens[1].endswith(".py"):
            raise ValueError(f"The app runs pytest itself, not scripts like {Path(tokens[1]).name}, so it can "
                             "collect the results. For example: python -m pytest tests")
        raise ValueError("The test command must run pytest, for example: python -m pytest tests")
    if tokens[0] in {"python", "python3", "py"}:
        tokens[0] = sys.executable  # the Python the app runs with, i.e. the project's virtual environment
    return env, tokens


def default_folder(project: Project) -> Path:
    """Where tests run unless the user picks a folder: the folder holding both steps and features."""
    try:
        return Path(os.path.commonpath([project.steps, project.features]))
    except ValueError:  # Windows: on different drives
        return Path(project.features)


def results_dir(project: Project) -> Path:
    return project_root(project) / ".gherkin-copilot" / "results"


def python_name(scenario: str) -> str:
    """pytest-bdd's test function name for a scenario."""
    name = re.sub(r"\W", "", scenario.replace(" ", "_"))
    return "test_" + re.sub(r"^\d+_*", "", name).lower()


def keyword_expression(wanted: set[str], collected: list[str]) -> str:
    """A ``-k`` expression that picks exactly these test names (``-k`` matches substrings)."""
    names = sorted({c.split("::")[-1].split("[")[0] for c in collected})
    found = sorted(w for w in wanted if w in names)
    if not found:
        raise LookupError
    others = [n for n in names if n not in found and any(w in n for w in found)]
    expression = " or ".join(found)
    return f"({expression}) and not ({' or '.join(others)})" if others else expression


@dataclass
class Run:
    project_id: str
    label: str
    started: float
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    status: str = "running"  # running / finished / stopped / error
    exit_code: int | None = None
    ended: float | None = None
    done: int = 0
    process: subprocess.Popen[str] | None = None
    stop_requested: bool = False

    def describe(self) -> dict[str, Any]:
        return {"label": self.label, "status": self.status, "exit_code": self.exit_code, "started": self.started,
                "ended": self.ended, "done": self.done, "log": list(self.log)[-400:]}


class Runner:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def status(self, project_id: str) -> dict[str, Any] | None:
        run = self._runs.get(project_id)
        return run.describe() if run else None

    def start(self, project: Project, scope: str, file: str = "", name: str = "") -> dict[str, Any]:
        if not project.test_command.strip():
            raise ValueError("Set this project's test command first (Details at the top).")
        env, args = parse_command(project.test_command)
        cwd = Path(project.run_from or default_folder(project))
        if not cwd.is_dir():
            raise ValueError(f"The folder to run tests from doesn't exist: {cwd}")
        wanted, label = self._scope(project, scope, file, name)
        with self._lock:
            current = self._runs.get(project.id)
            if current and current.status == "running":
                raise ValueError("Tests are already running for this project.")
            run = Run(project.id, label, time.time())
            self._runs[project.id] = run
        threading.Thread(target=self._work, args=(run, project, env, args, cwd, wanted), daemon=True).start()
        return run.describe()

    def stop(self, project_id: str) -> None:
        run = self._runs.get(project_id)
        if not run or run.status != "running":
            raise ValueError("Nothing is running.")
        run.stop_requested = True
        if run.process and run.process.poll() is None:
            run.process.terminate()

    @staticmethod
    def _scope(project: Project, scope: str, file: str, name: str) -> tuple[set[str] | None, str]:
        if scope == "all":
            return None, "All tests"
        path = path_inside(Path(project.features), file, ".feature")
        if not path.is_file():
            raise ValueError("That feature file no longer exists.")
        blocks = [b for b in gherkin.parse(path.read_text(encoding="utf-8")).scenarios]
        if scope == "file":
            if not blocks:
                raise ValueError("That feature file has no scenarios.")
            return {python_name(b.name) for b in blocks}, f"{file}"
        if scope == "scenario":
            if not any(b.name == name for b in blocks):
                raise ValueError("That scenario isn't in the file. Save your changes first.")
            return {python_name(name)}, f"{file} › {name}"  # noqa: RUF001 (a breadcrumb, shown to people)
        raise ValueError("Choose what to run.")

    def _exec(self, run: Run, args: list[str], env: dict[str, str], cwd: Path, deadline: float) -> int:
        process = subprocess.Popen(  # nosec B603: argument list from the user's own saved test command, no shell
            args, cwd=cwd, env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1)
        run.process = process
        timer = threading.Timer(max(deadline - time.time(), 1), process.terminate)
        timer.start()
        try:
            for line in process.stdout or ():
                line = line.rstrip("\n")
                run.log.append(line)
                if RESULT_LINE.match(line):
                    run.done += 1
            return process.wait()
        finally:
            timer.cancel()

    def _work(self, run: Run, project: Project, env: dict[str, str], args: list[str], cwd: Path,
              wanted: set[str] | None) -> None:
        deadline = run.started + TIME_LIMIT
        out = results_dir(project)
        try:
            if out.name == "results" and out.parent.name == ".gherkin-copilot" and out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            (out / ".gitignore").write_text("*\n", encoding="utf-8")
            extra = [f"--cucumberjson={out / 'cucumber.json'}", "-v"]
            if project.capture:
                extra += [f"--output={out / 'artifacts'}", "--screenshot=on", "--tracing=retain-on-failure"]
            if wanted is not None:
                run.log.append("$ collecting tests…")
                collect = Run(run.project_id, "", time.time())
                self._exec(collect, [*args, "--collect-only", "-q"], env, cwd, deadline)
                collected = [line for line in collect.log if "::" in line]
                try:
                    extra += ["-k", keyword_expression(wanted, collected)]
                except LookupError:
                    run.log.extend(list(collect.log)[-30:])
                    run.log.append("")
                    run.log.append("No test matches. Is the feature file loaded by a test module, e.g. "
                                   "scenarios(\"that.feature\") in a test_*.py file?")
                    run.status, run.ended = "error", time.time()
                    return
                if run.stop_requested:  # Stop was pressed while collecting: don't start the real run
                    run.log.append("Stopped before the tests started.")
                    run.status = "stopped"
                    return
            command = [*args, *extra]
            run.log.append(f"$ cd {cwd}")
            run.log.append("$ " + " ".join([*(f"{k}={v}" for k, v in env.items()), *command]))
            run.exit_code = self._exec(run, command, env, cwd, deadline)
            missing = next((line for line in run.log if "file or directory not found:" in line), None)
            if missing:
                run.log.append("")
                run.log.append(f"pytest couldn't find “{missing.split(':', 2)[-1].strip()}” from {cwd}. Set “Run from "
                               "folder” in Details to the folder you'd run this command from in a terminal.")
            run.status = "stopped" if run.stop_requested else "error" if time.time() > deadline else "finished"
            if run.status == "error":
                run.log.append(f"Stopped after the {TIME_LIMIT // 60}-minute time limit.")
        except OSError as exc:
            run.log.append(f"Couldn't start the tests: {exc}")
            run.status = "error"
        finally:
            run.ended = run.ended or time.time()
            if out.is_dir():
                (out / "run.json").write_text(json.dumps({k: v for k, v in run.describe().items() if k != "log"}),
                                              encoding="utf-8")


def latest_results(project: Project) -> dict[str, Any] | None:
    out = results_dir(project)
    report = out / "cucumber.json"
    run_file = out / "run.json"
    meta: dict[str, Any] = json.loads(run_file.read_text(encoding="utf-8")) if run_file.is_file() else {}
    if not report.is_file():
        return {"run": meta, "features": []} if meta else None
    try:
        features = load_report(report)
    except (ValueError, KeyError, TypeError):
        return {"run": meta, "features": [], "error": "The results file couldn't be read."}
    attach_artifacts(features, out / "artifacts")
    return {
        "run": meta,
        "folder": str(out),
        "features": [{
            "name": f.name, "uri": f.uri, "status": f.status,
            "scenarios": [{
                "name": s.name, "keyword": s.keyword, "status": s.status, "seconds": round(s.seconds, 2),
                "example": s.example, "tags": s.tags, "line": s.line,
                "steps": [{"keyword": st.keyword, "name": st.name, "status": st.status, "seconds": round(st.seconds, 2),
                           "error": short_error(st.error) if st.error else ""} for st in s.steps],
                "artifacts": {kind: [p.relative_to(out).as_posix() for p in paths]
                              for kind, paths in s.artifacts.items()},
            } for s in f.scenarios],
        } for f in features],
    }


ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webm": "video/webm", ".zip": "application/zip"}


def artifact(project: Project, relative: str) -> tuple[bytes, str]:
    out = results_dir(project).resolve()
    path = (out / relative).resolve()
    suffix = path.suffix.lower()
    if out not in path.parents or suffix not in ARTIFACT_TYPES or suffix not in ARTIFACT_KINDS \
            or not path.is_file():
        raise ValueError("No such file in the results.")
    return path.read_bytes(), ARTIFACT_TYPES[path.suffix.lower()]
