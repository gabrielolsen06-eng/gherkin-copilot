# Status of the 2026-10-11 review

The review itself ("gherkin-copilot: independent code review", 2026-10-11, scored 7.3 / 10) was
written against the 2026-10-09 copy: 35 tests, before the local web app existed. This file
records what was done about each finding on 2026-10-12. Every fix has a regression test.

## High

| # | Finding | Status | Commit |
|---|---|---|---|
| H1 | `check` exits 0 when the features path doesn't exist | Fixed. Missing paths exit 2 for `check`, `find`, `dupes`, `index` and `merge`; `check` also exits 2 when it finds no `.feature` files. CI checks this. | d56eec7 |
| H2 | Projects under a folder named `build`, `dist`, `venv`... index nothing | Fixed. Skip folders are matched below the project root only; the Python and feature scanners share one skip list. | d56eec7 |
| H3 | Output crashes on a Windows code page | Fixed. ✓ / ✗ fall back to `OK` / `MISSING` / `AMBIGUOUS`, other characters are replaced. Tests run on Windows in CI. | d56eec7 |
| H4 | Failed Cucumber-JVM/JS Background steps vanish from the report | Fixed. A Background element's steps are kept with the scenario that follows. | d56eec7 |
| H5 | Bad input files produce tracebacks | Fixed. One readable line and exit 2; internal errors exit 3. | d56eec7, 11e5f06 |

## Medium

| # | Finding | Status | Commit |
|---|---|---|---|
| M1 | `dupes` is O(n²) with SequenceMatcher (34 s for 1,000 steps) | Fixed. Cheap upper bounds skip pairs that can't reach the threshold: 15.9 s → 1.2 s on the same machine, identical results (tested against comparing every pair). | f58f989 |
| M2 | A stale saved index is used silently | Fixed. The index stores its root and creation time; a newer step file prints a note. Old index files still load. | d56eec7 |
| M3 | Plain-string pytest-bdd steps with `{}` treated as parse patterns | Fixed. Framework detected from the file's imports. | d56eec7 |
| M4 | Version number in two places | Fixed. `pyproject.toml` reads `gherkin_copilot.__version__`. | 11e5f06 |
| M5 | No licence | **Open, by choice:** waiting on a check of the owner's employment contract. | – |
| M6 | Few negative and edge-case tests | Fixed. Tests for every unhappy path above, plus seeded generated tests for the feature parser and parse patterns (no `hypothesis` dependency needed). 251 tests, 91% branch coverage. | d56eec7, 2b9e83b |
| M7 | A `Then` step performs an action | Fixed. `When I open the cart page` opens the cart; `Then the cart contains N items` only checks. | f485898 |

## Low

| # | Finding | Status | Commit |
|---|---|---|---|
| L1 | `cmd_check` complexity D (22) | Fixed: C (13). `cmd_merge` split the same way. | d56eec7, 49d47da |
| L2 | mypy `--strict` errors | Fixed: none (there were 56 by now, most in the app added after the review). | 11e5f06 |
| L3 | No ruff / mypy config | Fixed: in `pyproject.toml`; ruff and mypy are clean, and run in pre-commit and CI. | 11e5f06, ef6417a |
| L4 | Imperative login steps; mixed outcome phrasing | Fixed. A declarative login scenario sits next to the imperative ones; outcomes read "I see…", "the cart contains…". | f485898 |
| L5 | Generic top-level demo module names | Fixed. `demo/` is a package: `demo.server`, `demo.browsers`, `demo.steps.*`. | f485898 |
| L6 | `run_demo.py` treats any failure as the expected one | Fixed. Only exit code 1 counts as expected. | 11e5f06 |
| L7 | ```` ``` ```` doc string containing `"""`; `# language:`; first Examples row only | Doc strings fixed. `# language:` and "first Examples row only" documented in the README as limits. | 2b9e83b |
| L8 | Escaped `{{ }}` in parse patterns | Fixed. | 2b9e83b |
| L9 | Trace-viewer command relative to the build folder | Fixed. Absolute path. | 2b9e83b |
| L10 | Pylint R1729 on `run_demo.py` | Comment added explaining why the list stays a list. | 11e5f06 |

## Also done

- CI (`.github/workflows/ci.yml`): ruff, mypy, bandit, pip-audit; tests on Ubuntu, Windows and
  macOS with Python 3.11 and 3.13; the browser demo with its report as an artifact.
- Pre-commit hooks (ruff, mypy, bandit) and pinned dev tools in `requirements-dev.txt`.
- Bandit fully clean; displayed paths use forward slashes on every platform.
- The long-failing Brave detection test no longer depends on what's installed.

## Still open

- **Git hosting:** not on GitHub yet, so CI hasn't run there. Windows results are untested
  until it does.
- **Licence (M5):** see above.
- **Complexity:** four functions in the app (added after the review) are rated D by radon:
  `ui/gherkin.parse`, `ui/runner.Runner._work`, `ui/server.App.post`, `ui/builder.plan_stubs`.
