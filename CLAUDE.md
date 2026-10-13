# gherkin-copilot

A Python CLI that helps write Gherkin/BDD tests faster by reusing existing step
definitions, plus a runnable pytest-bdd + Playwright demo. Personal portfolio project,
meant to be shown at work and installed on a locked-down work laptop.

## Hard constraints

- **The tool (`src/gherkin_copilot`) uses the standard library only.** No runtime
  dependencies in `pyproject.toml`. It never imports or executes scanned code (parse with
  `ast`), makes no network calls, and never spawns processes, with one exception:
  `ui/runner.py` runs the user's own saved pytest command (argument list, no shell) when
  they press Run in the app. Keep it that way: nothing else may start a process.
- **Nothing may require an extra download at run time.** Demo dependencies are pinned
  with hashes in `requirements.lock`. Don't add Playwright browser or ffmpeg downloads to
  the default path (that's why videos are opt-in via `--videos`).
- **Keep `SECURITY.md` accurate.** Update its "Checks run" table when tests, dependencies
  or behaviour change.
- The demo shop (`demo/app`) must stay offline: no CDN assets, `textContent` only, served
  on 127.0.0.1 by `demo/server.py`. Its DOM mirrors saucedemo.com.

## Commands

```bash
source .venv/bin/activate
python -m pytest tests                    # tool unit tests
python run_demo.py --brave --headed       # full demo in Brave (owner's browser on macOS)
python run_demo.py --brave --no-open      # same, without opening the HTML report
python -m pytest desktop_demo             # desktop scenarios: Windows only (skip on macOS); CI job "desktop"
gherkin-copilot init|index|find|dupes|merge|check|report|ui --help
gherkin-copilot ui                        # the app, on 127.0.0.1:8765
```

After changing `src/`, reinstall with `python -m pip install --no-deps --no-build-isolation .`

### Quality checks (dev-only tools in `.venv-dev`, never runtime dependencies)

```bash
.venv-dev/bin/ruff check src tests demo desktop_demo run_demo.py && .venv-dev/bin/mypy
.venv-dev/bin/bandit -q -r src demo desktop_demo run_demo.py && .venv-dev/bin/pip-audit --strict -r requirements.lock -r requirements-desktop.lock
```

All four must stay clean. Exit codes: 0 ok, 1 finding, 2 bad input (`UsageError`; wrap loaders in
`cli._input_file()` to turn their ValueError into one), 3 internal error. Displayed paths use `.as_posix()`. CI: `.github/workflows/ci.yml`.

### Dependencies

Edit `requirements.in`, then regenerate the lock with pip-tools (in a separate venv):
`pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in`.
Keep `colorama` pinned (pytest needs it on Windows). Run `pip-audit -r requirements.lock`
and `bandit -r src demo run_demo.py` afterwards. Then copy the lock to
`src/gherkin_copilot/starter/requirements.lock.tmpl` and the pins to `requirements.in.tmpl`
(the project `init` creates), and the Playwright image tag in `starter/gitlab-ci.yml.tmpl`
and `.github/workflows/ci.yml` if playwright changed; `tests/test_init.py` fails until all match.

## Layout

- `src/gherkin_copilot/`: `scaffold.py` + `starter/` (`init`: starter project from `*.tmpl`
  files shipped as package data; only creates files, never overwrites), `indexer.py` (find step defs via ast), `matcher.py` (exact and
  fuzzy matching), `gherkin.py` (the one feature-file parser: exact scenario line spans + renderer), `checker.py` (steps
  from `gherkin.py`, binding, stubs), `merger.py` (merge duplicate
  steps; the only code that edits user files, and only with `--apply`), `report.py` (Cucumber JSON
  to HTML, no JS), `cli.py`, `ui/` (the local web app: `server.py` stdlib HTTP server with
  token + Host/Origin checks, `projects.py` project list in `~/.gherkin-copilot/`,
  `catalog.py` steps as text + slots + `vscode://` links to their code (no editing step code in the app), `builder.py` check/save/stubs for the scenario
  builder (preview diff + hash, writes only inside project folders), `dupes.py` duplicates
  screen on top of `merger.plan_merges` (one pair at a time, multi-file hash), `editor.py`
  scenario list/open-as-blocks/new/edit/delete/duplicate/restore (validates a structured
  spec, never writes raw browser text), `history.py` append-only
  `.gherkin-copilot/history.jsonl` in the project, `runner.py` runs the project's test
  command and reads `cucumber.json` back, `home.py` home page statistics, `gitinfo.py` branch/commit/pull/ahead-behind
  read straight from `.git` (refs, reflog, zlib loose objects and v2 packs; never runs git), `stepcode.py` action blocks → Playwright step code
  (fixed action/locator menu, `repr` for all user text, compiled before saving), `static/` plain
  HTML/CSS/JS, `textContent` only, no frameworks or CDNs)
- `demo/`: `app/` practice shop, `features/`, `drafts/` (deliberately unimplemented
  checkout scenario), `steps/` (two deliberate near-duplicate steps for the `dupes`/`merge` demo; `cart.feature`
  uses one so `merge` rewrites a line),
  `conftest.py` (loads every file in `steps/`), `browsers.py` (finds Brave), `server.py`. `demo/` is a
  package: import as `demo.server`, `demo.browsers`, `demo.steps.*`
- `tests/`: unit tests for the tool
- `desktop_demo/`: Windows desktop testing proof of concept, a separate project (own pytest.ini,
  conftest, steps, features) so its steps never clash with `demo/`. `uia.py` drives apps through
  Windows UI Automation via `comtypes` (imported lazily, so it loads on macOS; the test module
  skips off Windows; a skip in conftest.py would crash). App from `DESKTOP_APP` (default
  Character Map), `DESKTOP_WINDOW` for launchers. Actions run in a background thread (invoke
  blocks while a message box is open). `requirements-desktop.lock` is hand-written: one
  pure-Python package; update its hashes from PyPI's JSON when bumping, never via pip-compile
  on macOS (it drops Windows-only packages). Desktop failures show as annotations on the CI run.

## Conventions

- Owner's environment: macOS, Brave (no Chrome), Python 3.14. The work laptop may be
  Windows with Edge.
- Python 3.11+. Type hints, dataclasses, small functions, comments only where non-obvious.
- Every new command gets `--json` output and tests in `tests/`.

## Roadmap

In progress, the app (`gherkin-copilot ui`), built in stages: 1. server, projects, step
catalogue (done); 2. block-based scenario builder with live check, stubs, save to
.feature (done; writes only after the user confirms a preview); 3. duplicates & merge
(done; `merge --features` supports features outside the steps folder); 3b. scenario
editor + history (done); 4. test results with a Run button (done: whole suite, one feature
file, or the scenario being edited; results in `.gherkin-copilot/results`, self-ignored);
5. home page: stats, git, recent changes (done); 6. docs/security pass (re-run bandit and pip-audit).


1. Done: `index`, `find`, `dupes`, `merge`, `check --stubs`, `report`, runnable demo
2. Done: GitHub repo with CI (GitHub Actions, Ubuntu + Windows)

**No LLM features** (dropped the planned `generate STORY.txt`): the tool has to run on a
locked-down, low-powered work laptop, so no network models and no local models either.
Don't propose them.
