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
gherkin-copilot ui                        # the app, on 127.0.0.1:8765
```

After changing `src/`, reinstall with `rm -rf build && python -m pip install --no-deps --no-build-isolation .`
(a stale `build/` would ship deleted modules in the installed copy).

### Quality checks (dev-only tools in `.venv-dev`, never runtime dependencies)

```bash
.venv-dev/bin/ruff check src tests demo desktop_demo run_demo.py && .venv-dev/bin/mypy
.venv-dev/bin/bandit -q -r src demo desktop_demo run_demo.py && .venv-dev/bin/pip-audit --strict -r requirements.lock -r requirements-desktop.lock
```

All four must stay clean. So must VS Code's Pylance, which reads `[tool.pyright]` in `pyproject.toml`
(strict everywhere but `tests/`): `npx pyright@1.1.414 --pythonpath .venv/bin/python`, also with
`--pythonplatform Windows`. Windows-only code starts with `if sys.platform != "win32": raise OSError(...)`
so Pyright checks it on Windows and skips it on macOS (not `assert`: bandit flags it). Exit codes: 0 ok, 1 finding, 2 bad input (`UsageError`; wrap loaders in
`cli._input_file()` to turn their ValueError into one), 3 internal error. Displayed paths use `.as_posix()`. CI: `.github/workflows/ci.yml`.

Changing dependencies: follow the `update-dependencies` skill (`.claude/skills/`).

## Layout gotchas

Read the tree for what each module does; these are the rules the code doesn't show.

- `gherkin.py` is the one feature-file parser. Comments above a step are kept; other lines it can't write back
  go in `Block.unkept`, and the editor refuses those scenarios.
- Every write of a user's file goes through `files.save_text` (keeps line endings, atomic); check links with
  `resolves_inside`. `merger.py` is the only CLI code that edits user files, and only with `--apply`;
  `init` (`scaffold.py`) only creates files, never overwrites.
- The app (`ui/`): no editing step code in the app (`catalog.py` links to it with `vscode://`); writes only after
  the user confirms a preview, and only inside project folders; `editor.py` validates a structured spec and never
  writes raw browser text; `stepcode.py` uses a fixed action/locator menu, `repr` for all user text, and compiles
  before saving; `gitinfo.py` never runs git; `static/` is `textContent` only, no frameworks or CDNs.
- `project_root`: steps/features side by side count as one project only if their parent has a pytest config or
  conftest.py, else the features folder. History, results (scratch: replaced by the next run) and test records
  (`test-records/`: never overwritten or deleted; screenshots of failures only, no traces) live there. So do
  test settings (`.gherkin-copilot/settings.json`, `ui/settings.py`): they win over `projects.json`, and
  `App.project()` applies them, so take projects from there.
- `demo/` is a package: import as `demo.server`, `demo.browsers`, `demo.steps.*`. `drafts/` is deliberately
  unimplemented, and `steps/` has two deliberate near-duplicate steps for the `dupes`/`merge` demo
  (`cart.feature` uses one so `merge` rewrites a line): don't "fix" them. Its `conftest.py` registers every
  feature-file tag as a pytest marker, as the starter conftests do, so a tag added in the app can't break collection.
- CI's lint job runs ruff and bandit on freshly generated web and desktop projects, so template code is checked
  too. `tests/test_app_server.py` imports the generated project's `app_server.py`.
- `desktop_demo/` is exactly the project `init --desktop` creates: its files are copied to
  `src/gherkin_copilot/starter_desktop/*.tmpl` (plus a README template) and `test_init.py` fails until they match.
  Standalone project (no `__init__.py`; `import uia`, `steps.*`); `comtypes` is imported lazily so it loads on
  macOS. Scenarios skip off Windows via `pytestmark` (a whole-module skip exits 5, a skip in conftest.py crashes).
  Actions run in a background thread (invoke blocks while a message box is open). Keep the `uia.*` function names
  that `stepcode.DESKTOP_ACTIONS` calls stable. `requirements-desktop.lock` is hand-written: update its hashes from
  PyPI's JSON, never via pip-compile on macOS (it drops Windows-only packages).

## Conventions

- Owner's environment: macOS, Brave (no Chrome), Python 3.14. The work laptop may be
  Windows with Edge.
- Python 3.11+. Type hints, dataclasses, small functions, comments only where non-obvious.
- Every new command gets `--json` output and tests in `tests/`.

## Not planned

**No LLM features** (dropped the planned `generate STORY.txt`): the tool has to run on a
locked-down, low-powered work laptop, so no network models and no local models either.
Don't propose them.
