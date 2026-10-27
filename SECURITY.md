# Security notes

This page is written for whoever approves software on a managed work laptop.

## What gherkin-copilot does and doesn't do

- **Reads source files, never runs them.** Step definitions are found by parsing Python
  files with the standard library's `ast` module. Scanned code is never imported or
  executed, so pointing the tool at a project can't trigger side effects.
- **Warns about passwords written in files.** `check` also reads the step code's Python files
  as text and warns about anything that looks like a password: a quoted value after
  "password" in a feature file, a table column named password, or a password set to a
  literal in Python. The warning shows the value as `***` and doesn't change the exit code,
  as it's a guess. (The list of steps above it still shows each feature-file line as
  written.) Public practice passwords and deliberately wrong ones (`secret_sauce`, `wrong`,
  `invalid`, `incorrect`) are allowed, and `# nosec` skips a Python line. The web project
  `init` creates reads test logins from environment variables (`TEST_USER_<ROLE>`,
  `TEST_PASSWORD_<ROLE>`), never from its files.
- **No network access.** The tool makes no HTTP requests, sends no telemetry and has no
  update check.
- **No third-party runtime dependencies.** `pyproject.toml` lists none; it uses only the
  Python standard library.
- **Writes files only when asked.** `index` writes `.gherkin-index.json` (plain JSON
  data, loaded with `json.loads`, never `pickle` or `eval`). Use `--no-save` to avoid even
  that. `merge` edits `.feature` and step files only with `--apply`; without it, it prints
  the planned diff and changes nothing. It only edits files inside the folder it's given,
  and refuses a merge it can't show is safe: the two steps' code differs, they convert values
  differently (`{count}` vs `{count:d}`), a function is used elsewhere, or a line wouldn't
  match the kept step. Every file it writes keeps its line endings and is replaced in one
  step (written to a temporary file first), so a crash can't leave half a file. `init` creates a starter
  project's files inside the folder it's given; it never overwrites or edits a file that
  already exists (each file is opened in create-only mode), and `--dry-run` writes nothing.
  The files are fixed templates that ship with the tool; the only value filled in is the
  project name, in its README.
- **No shell commands.** The tool starts only two programs: your web browser, through
  Python's standard `webbrowser` module, when `ui` starts (skip it with `--no-open`) or when
  you ask `report` to `--open` a report; and the test command you saved for a project, when
  you press the app's **Run tests** button (see below). `run_demo.py` runs Python
  subprocesses with argument lists, never through a shell.

## The app (`gherkin-copilot ui`)

- **Local only.** It listens on `127.0.0.1`, so other machines can't connect to it. It
  stops when you press Ctrl+C or close the terminal.
- **Protected against other websites.** A web page open in the same browser can't use the
  app: every request that changes something must carry a random token that's created
  fresh each time the app starts and only given to the app's own page, and must come from
  the app's own address. Requests addressed to any other host name are refused, which
  blocks DNS-rebinding attacks.
- **Serves a fixed list of files.** The app's own page, script, stylesheet and icon, and the
  screenshots, videos and traces of the last test run in the project's
  `.gherkin-copilot/results` folder (see **Runs tests** below); nothing else from your disk. Pages have a strict `Content-Security-Policy` (no inline or
  external scripts, no external connections) and build all text with `textContent`, so
  step text from a project can never run as code.
- **The home page reads git's files; it never runs git.** To show the branch, latest
  commit, last pull and commits to push or pull, it reads the project's `.git` folder
  directly: `HEAD`, branch refs, the reflog (`logs/HEAD`) and commit objects, unpacked with
  the standard library's `zlib` and capped at 1 MB each. It never writes there and never
  contacts the remote, so "to pull" is as of your last fetch. User names and tokens in
  URLs are removed from reflog messages before they're shown, and remote URLs from
  `.git/config` are never read out. As each test run starts, the same reader compares the
  files that define the tests with the commit: it reads the commit's tree objects (only the
  folders on the way to those files) and the tags (`refs/tags`, `packed-refs`).
- **Step code is edited in your editor, not in the app.** The app has no way to type Python:
  to fix a step's code, **Open in VS Code** is a `vscode://` link to the step's file and line,
  built by the server from the project's own folder. Following it is the browser's job (it
  asks before opening VS Code); the app itself starts nothing.
- **What it writes, and when:**
  - the project list, `~/.gherkin-copilot/projects.json` (names and folder paths).
    Removing a project from the list never touches its files;
  - the project's test settings, `.gherkin-copilot/settings.json` in the project folder, when
    you press **Save test settings** in Details: the test command, the folder to run it from
    (relative to the project) and the screenshots setting. The change is logged in the
    history. The file is meant to be committed, so a command that sets an environment
    variable whose name contains PASS, SECRET, TOKEN or KEY is refused;
  - a scenario into a `.feature` file, and step templates into an existing step file, but
    only when you press Save or Add, after the app has shown you exactly what will change.
    The server re-checks the file when you confirm and refuses if it changed since the
    preview. It only writes inside the selected project's folders (a link that points outside
    them is refused): new files only as plain
    names in the features folder, appends only to existing `.feature` files, and step
    templates only to `.py` files that already contain step definitions. The step code it
    writes is generated on the server from the scenario text, never taken from the
    browser;
  - a merge of two duplicate steps (rewritten feature-file lines and the removed step),
    with the same preview, confirm and changed-since-preview checks, and the same safety
    rules as the command line `merge`. All the files a merge touches are checked together.
    The one exception is **Merge anyway**, offered only when all that differs is the two
    functions' code (bodies, parameters or decorator options): the app shows both functions
    first and the user decides they do the same thing. It never overrides the other checks
    (a function called or imported elsewhere, values that can't be carried across, lines
    that wouldn't match the kept step), and the server re-checks that on save;
  - edits, deletions, duplicates and restores of a single scenario in a `.feature` file.
    The browser sends the scenario as structured data (name, tags, steps, Examples), which
    the server validates and writes itself; it never writes text from the browser
    directly. Only the lines of that one scenario change, and saving is refused if the
    scenario or the file changed since the preview;
  - step code built with action blocks, added to an existing step file or a new `.py`
    file in the steps folder. The browser sends a list of actions from a fixed menu
    (click, type into, check text…) with the element and text to use; the server writes
    the Python. Everything typed is placed inside quoted Python strings, value names must
    be plain identifiers, and the result is compiled and checked to match the scenario line
    before it can be saved, so no text from the browser can become code;
  - a history of those changes, appended to `.gherkin-copilot/history.jsonl` in the
    project folder: the folder holding both the steps and features folders, if one holds the
    other or they sit side by side in a pytest project (one with `pytest.ini`,
    `pyproject.toml`, `tox.ini`, `setup.cfg` or `conftest.py`); otherwise the features
    folder. The test results and test records below go in the same folder. It holds the
    diffs, the scenario text before and after, and the name the user typed in. It never contains anything beyond what's already in the project's files.
  - a test record, only when you press **Save as test record** on a run that finished: a
    new folder, `test-records/<date>_<title>/` in the project folder, holding a copy of the run's
    `cucumber.json`, a `report.html` made from it (the same no-script page as `report`), a
    `record.json` with the title, environment and notes you typed, your name, the
    computer's operating system, the test command that ran (with the values of variables
    whose names look like secrets shown as `***`), the branch and commit read from `.git`, pytest's exit code
    and the Python, pytest and plugin versions pytest printed, a SHA-256 fingerprint of
    every file that defines the tests (feature, Python, pytest configuration and requirements
    files, the test settings and `data/`), taken as the run started, whether those files
    were the commit's (a file changed since the commit or not in it, a branch other than
    `main`, `master` or `release/…`, or no git at all is listed as a reason: the record is
    still saved, and the Save dialog shows the reasons first), and screenshots
    of the scenarios that didn't pass. A record only says it passed if pytest's exit code
    agrees with the results file; if they disagree it says "Verdict doesn't match pytest"
    (result `mismatch`), so a mistake in the tool can't turn a failed run into a pass.
    Traces and videos are never copied, as they can hold
    whatever a test typed. It never overwrites or deletes a record, refuses a `test-records`
    link that points outside the project, and refuses if the results changed since the page
    showed them. Records are meant to be committed, so their screenshots are shared with
    everyone who can read the repository: look at them first if the suite runs against a
    real application.
- **Reads projects the way the command line does:** with `ast`, never importing or running
  their code.
- **Opens your browser** with Python's standard `webbrowser` module (use `--no-open` to
  skip).
- **Runs tests only when you ask, and only the project's own command.** A project can have a
  test command (for example `python -m pytest tests`), set in the project's details and
  saved in its `.gherkin-copilot/settings.json`, which is meant to be committed (a project
  without that file uses the command an earlier version saved in your
  `~/.gherkin-copilot/projects.json`). So whoever can commit to a project decides what Run
  runs, as they already decide what its `conftest.py` does: pytest runs that on every run.
  Until a command is set, the app can't run anything. When you press Run, the app:
  - checks that the command runs pytest (`pytest …` or `python -m pytest …`), optionally
    preceded by `NAME=value` environment settings. That check catches typing mistakes; it
    isn't a security boundary. pytest runs the project's own `conftest.py` and test code
    with your rights, as it would in a terminal, and settings such as `PYTEST_ADDOPTS` change
    what it does. The session token is what stops anyone else pressing Run: other websites
    can't send it;
  - splits the command into an argument list and starts it directly, never through a
    shell, so characters like `;`, `|` or `&&` have no special meaning;
  - runs it from the folder you chose, at most one run per project, with a Stop button and
    a 30-minute limit;
  - adds only pytest options that tell it where to write results (and, if ticked,
    screenshots and traces), into the project's `.gherkin-copilot/results` folder. That
    folder is cleared before each run and contains a `.gitignore` so it's never committed.
  The results page shows those files; the app serves only images, videos and traces from
  inside that folder. Setting or changing the command needs the same session token as
  every other change.

## Test reports

- **`report.html` contains no JavaScript.** It has a strict `Content-Security-Policy` that blocks
  all scripts and network requests, and only shows local images and videos. Every value taken
  from the test results (step names, error messages) is HTML-escaped.
- **The tool uploads nothing.** Screenshots, videos and traces are written to the local
  `reports/` folder. The trace viewer command serves on `127.0.0.1` only.
- **Recordings can include whatever was on the page, and traces hold secrets.** A trace records
  everything a scenario typed, passwords included (masking a CI variable only hides it in the
  job log), and the site's cookies. The demo shop shows no real data. If you point the suite at
  a real application, treat `reports/` like a password. It is excluded from git by
  `.gitignore`. The GitLab pipeline that `init --ci gitlab` writes keeps `reports/` as a job
  artifact for 30 days, downloadable only by project members with Developer role or above
  (`artifacts:access`, GitLab 16.11 or later).

## The demo

- **The practice shop runs on `127.0.0.1` only.** It is never reachable from the network,
  and the server stops when the tests finish.
- **The server is read-only and serves static files.** It disables directory listings,
  rejects POST requests, and sends `Content-Security-Policy`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store`.
- **No external resources.** The shop loads no CDN scripts, fonts or images. Its pages
  build content with `textContent` only, never `innerHTML`.
- **No real credentials.** The only password in the project is `secret_sauce`. It is a
  public practice password printed on the demo shop's and saucedemo.com's login pages.
  It is marked `# nosec` with that justification.
- **No internet needed.** Point the suite at an external site only if you choose to, with
  `DEMO_BASE_URL`.

## Desktop testing (`init --desktop`, `desktop_demo/`)

The tool itself stays standard-library only and starts no programs. Desktop testing lives in
the test project: `init --desktop` writes its files from fixed templates (the same as
`desktop_demo/` in this repository), and its steps drive a Windows program the way a screen
reader does.

- **What it could do:** UI Automation (built into Windows) can read and press the controls of
  **any window the test user can see**, not just the app under test. Run desktop tests on a test
  VM, not on a machine with real work or sensitive data open.
- **What it does:** starts the configured `.exe` (`desktop_app` in `pytest.ini`, or
  `DESKTOP_APP`) as an argument list, never through a shell; only touches the windows of that
  app's process; and closes the app after every scenario, ending its process if it won't close.
- **Protects open work:** if the app is already open, the scenario stops instead of taking
  over that window (and later closing it, losing anything unsaved).
- **One new package, Windows only:** `comtypes` 1.4.17 (MIT licence, pure Python, no
  dependencies, no compiled files), pinned with SHA-256 hashes in
  `requirements-desktop.lock`. It talks to Windows' own `UIAutomationCore.dll`, which
  Microsoft signs. (`uiautomation`, a package that does the same job, isn't used: it ships two
  unsigned DLLs.)
- **Writes generated files once:** the first time it runs, `comtypes` writes Python wrapper
  code for UI Automation into its own cache folder.
- **No network, no downloads at run time.** Character Map, which the CI scenarios use, is
  part of Windows.
- **Action blocks for desktop steps** follow the same rules as the browser ones: a fixed menu
  (click, tick, type into, check what a field shows, check text appears), every typed name and
  value placed inside a quoted Python string, and the result compiled and checked against the
  scenario line before it can be saved. The generated code only calls the project's own
  `uia.py`.

## Starting the site under test (`app_server`)

For a site that a program on the test machine starts (an `.exe` that runs a local server, say),
the web project `init` creates can start and stop that program. This is the test project's own
code (`app_server.py`, written by `init`), not the tool's.

- **Only when asked:** nothing happens unless `app_server` is set in the project's `pytest.ini`
  (or `APP_SERVER`). The program is started as an argument list, never through a shell.
- **Talks only to `base_url`,** to see whether the site answers yet. It sends nothing else,
  accepts only `http://` and `https://` addresses, and bypasses any proxy so the check stays on
  the machine.
- **Stops everything it started:** on Windows the program runs in a job object (a group that
  includes every program it starts), so ending the job also ends a server the program left
  running after it closed itself, and Windows ends them anyway if the tests crash. Elsewhere it
  runs in its own process group.
- **Never tests a stale site:** if something already answers at `base_url` before the start,
  the run stops with a message.
- **Output stays local,** in `reports/app_server.log`, which the project's `.gitignore` excludes.

## Dependencies

- `requirements.lock` pins **every** package (direct and transitive) to an exact version
  with **SHA-256 hashes** for every published file. Installing with `--require-hashes`
  makes pip refuse anything that has been altered or isn't on the list.
- Direct dependencies are kept to the minimum needed: `pytest`, `pytest-bdd`,
  `pytest-playwright` and `playwright`. Each is widely used and maintained, and listed in
  `requirements.in`.
- `requirements-desktop.lock` (Windows desktop testing only) pins one pure-Python package,
  `comtypes`, with the hashes PyPI publishes for it. It has no dependencies, so the file is
  complete as written; it's installed alongside `requirements.lock`.
- **Browsers:** Playwright can drive the Edge or Chrome already installed on the machine
  (`--browser-channel msedge` / `chrome`) or Brave (`--brave`), so nothing has to be
  downloaded. Downloading Playwright's own browser (`playwright install`) is optional.

### Installing without internet access

On a machine that can reach PyPI, download the exact files for the target platform:

```bash
python -m pip download --require-hashes -r requirements.lock -d wheelhouse \
    --only-binary=:all: --platform win_amd64 --python-version 3.12
```

Copy `wheelhouse/` across with the project, then on the work laptop:

```bash
python -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation .
```

If your company runs an internal package mirror (Artifactory, Nexus, Azure Artifacts),
point pip at it with `--index-url`; the hashes still guarantee the same files.

## Known gaps

- **No human security review yet.** The reviews so far were automated or AI-assisted, and most
  of the code was written with an AI coding assistant (the commit history says so). A review by
  your security team against this page is the next step.
- **No licence yet.** The repository has no LICENSE file until the owner has confirmed who owns
  the code.
- **CI actions are pinned to major versions** (for example `@v7`), and the Playwright image in
  the GitLab template to a version tag, not to exact commits or digests.
- **Dependency updates are manual.** `pip-audit` runs on every push and once a week (CI's
  schedule), so a new vulnerability in a pinned package fails a run, but updating the pin is
  done by hand.
- **Desktop tests can control any window the test user can see** (see above). Run them on test
  VMs only.
- **Playwright starts its own helper program,** a bundled Node.js driver (`node.exe` inside the
  playwright package), to control the browser. Strict application control can block it.

## Checks run on this version

Results from 2026-10-27 (CI on the release commit for the browser and desktop rows; the local
Brave run of a project made by `init` is from 2026-10-16). The independent review in `reviews/` and its re-review were
worked through on 2026-10-12 (see `gherkin-copilot-review-2026-10-11-status.md` and
`gherkin-copilot-review-2026-10-12-status.md`); the app's home page, desktop testing,
`app_server`, test records, the password warnings, test settings in the project, fingerprints
and the version-control check were added after them:

| Check | Result |
|---|---|
| `pip-audit --strict -r requirements.lock -r requirements-desktop.lock` (known vulnerabilities) | No known vulnerabilities found (setuptools moved to 84.2.1 for GHSA-xmj3-9gf7-h52w on 2026-10-27) |
| `bandit -q -r src demo desktop_demo run_demo.py`, and on both kinds of project `init` creates (static security analysis) | No findings. The places that start a process (`run_demo.py`, the app's Run button in `ui/runner.py`, and the desktop steps starting the app under test) use fixed argument lists and no shell, and are annotated with why |
| `mypy --strict` | No issues in 26 source files |
| `ruff check` (rules in `pyproject.toml`), also on both kinds of project `init` creates | No issues |
| Pyright, the type checker in VS Code (settings in `pyproject.toml`): strict on `src`, `demo`, `desktop_demo` and both kinds of project `init` creates, standard on `tests/`, checked as macOS, Windows and Linux | No errors |
| Tool tests | 341 passed (macOS, Python 3.14; also run on Windows and Ubuntu in CI), including tests for missing paths, malformed files, non-finite numbers, Windows code pages, generated inputs, the project `init` creates, and the home page's git reader checked against git itself (every object in a delta-compressed pack, and commits to push/pull on branchy history) |
| Branch coverage (`coverage run -m pytest tests`) | 93% |
| Demo scenarios in a real browser (Brave) | 6 passed |
| Desktop scenarios on Windows (`desktop_demo/`, Character Map, CI `windows-latest`) | 3 passed: typing into a field, clicking a button and ticking a checkbox, through UI Automation with no fake mouse or keyboard. A project made by `init --desktop` passes the same scenarios as created |
| A project made by `init`, run in a real browser | Passes in Brave locally, and in CI in Chromium and in the installed Edge on Windows (no browser download), with warnings treated as errors. Its `requirements.lock` is identical to this repo's (a test checks), so the audit above covers it |
| `pip install --require-hashes -r requirements.lock` in a clean venv, then `pip check` | Installs cleanly, no broken requirements (checked 2026-10-27, after the setuptools update) |

Ruff, mypy, bandit and the tests also run as pre-commit hooks, and the same checks run on
every push in CI (`.github/workflows/ci.yml`), including the tests on Windows. To repeat them
yourself, see "Development" in the README.

**Error handling:** the command line tool turns bad input (missing paths, files that
aren't valid JSON or UTF-8) into a one-line message with exit code 2, and never prints a
traceback for it (OWASP ASVS V7.4). Only the file loaders' errors count as bad input:
unexpected internal errors print their details and exit with 3, so a bug can't pass for a
user mistake.

## Reporting a problem

If you find a security issue, please open a private report to the repository owner rather
than a public issue.
