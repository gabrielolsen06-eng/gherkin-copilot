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
gherkin-copilot index|find|dupes|merge|check|report|ui --help
gherkin-copilot ui                        # the app, on 127.0.0.1:8765
```

After changing `src/`, reinstall with `python -m pip install --no-deps --no-build-isolation .`

### Dependencies

Edit `requirements.in`, then regenerate the lock with pip-tools (in a separate venv):
`pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in`.
Keep `colorama` pinned (pytest needs it on Windows). Run `pip-audit -r requirements.lock`
and `bandit -r src demo run_demo.py` afterwards.

## Layout

- `src/gherkin_copilot/`: `indexer.py` (find step defs via ast), `matcher.py` (exact and
  fuzzy matching), `checker.py` (feature-file parsing, stubs), `merger.py` (merge duplicate
  steps; the only code that edits user files, and only with `--apply`), `report.py` (Cucumber JSON
  to HTML, no JS), `cli.py`, `ui/` (the local web app: `server.py` stdlib HTTP server with
  token + Host/Origin checks, `projects.py` project list in `~/.gherkin-copilot/`,
  `catalog.py` steps as text + slots, `builder.py` check/save/stubs for the scenario
  builder (preview diff + hash, writes only inside project folders), `dupes.py` duplicates
  screen on top of `merger.plan_merges` (one pair at a time, multi-file hash),
  `gherkin.py` feature-file parser with exact scenario line spans + renderer, `editor.py`
  scenario list/open-as-blocks/new/edit/delete/duplicate/restore (validates a structured
  spec, never writes raw browser text), `history.py` append-only
  `.gherkin-copilot/history.jsonl` in the project, `runner.py` runs the project's test
  command and reads `cucumber.json` back, `stepcode.py` action blocks → Playwright step code
  (fixed action/locator menu, `repr` for all user text, compiled before saving), `static/` plain
  HTML/CSS/JS, `textContent` only, no frameworks or CDNs)
- `demo/`: `app/` practice shop, `features/`, `drafts/` (deliberately unimplemented
  checkout scenario), `steps/` (two deliberate near-duplicate steps for the `dupes`/`merge` demo; `cart.feature`
  uses one so `merge` rewrites a line),
  `conftest.py`, `browsers.py` (finds Brave), `server.py`
- `tests/`: unit tests for the tool

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
5. docs/security pass (re-run bandit and pip-audit).


1. Done: `index`, `find`, `dupes`, `merge`, `check --stubs`, `report`, runnable demo
2. Next: `generate STORY.txt`: draft scenarios from a user story with an LLM, using only
   existing steps (must be optional and clearly network-using; keep the core offline)
3. Put it in a GitHub repo with CI (GitHub Actions)
