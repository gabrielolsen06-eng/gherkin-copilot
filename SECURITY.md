# Security notes

This page is written for whoever approves software on a managed work laptop.

## What gherkin-copilot does and doesn't do

- **Reads source files, never runs them.** Step definitions are found by parsing Python
  files with the standard library's `ast` module. Scanned code is never imported or
  executed, so pointing the tool at a project can't trigger side effects.
- **No network access.** The tool makes no HTTP requests, sends no telemetry and has no
  update check.
- **No third-party runtime dependencies.** `pyproject.toml` lists none; it uses only the
  Python standard library.
- **Writes files only when asked.** `index` writes `.gherkin-index.json` (plain JSON
  data, loaded with `json.loads`, never `pickle` or `eval`). Use `--no-save` to avoid even
  that. `merge` edits `.feature` and step files only with `--apply`; without it, it prints
  the planned diff and changes nothing. It only edits files inside the folder it's given,
  and refuses any merge that could change what a test does.
- **No shell commands.** The command line tool never starts other programs. The only
  exception is the app's **Run tests** button (see below), which runs the test command you
  saved for a project. `run_demo.py` runs Python subprocesses with argument lists, never
  through a shell.

## The app (`gherkin-copilot ui`)

- **Local only.** It listens on `127.0.0.1`, so other machines can't connect to it. It
  stops when you press Ctrl+C or close the terminal.
- **Protected against other websites.** A web page open in the same browser can't use the
  app: every request that changes something must carry a random token that's created
  fresh each time the app starts and only given to the app's own page, and must come from
  the app's own address. Requests addressed to any other host name are refused, which
  blocks DNS-rebinding attacks.
- **Serves a fixed list of files.** Only the app's own page, script, stylesheet and icon,
  never files from your disk. Pages have a strict `Content-Security-Policy` (no inline or
  external scripts, no external connections) and build all text with `textContent`, so
  step text from a project can never run as code.
- **What it writes, and when:**
  - the project list, `~/.gherkin-copilot/projects.json` (names and folder paths).
    Removing a project from the list never touches its files;
  - a scenario into a `.feature` file, and step templates into an existing step file, but
    only when you press Save or Add, after the app has shown you exactly what will change.
    The server re-checks the file when you confirm and refuses if it changed since the
    preview. It only writes inside the selected project's folders: new files only as plain
    names in the features folder, appends only to existing `.feature` files, and step
    templates only to `.py` files that already contain step definitions. The step code it
    writes is generated on the server from the scenario text, never taken from the
    browser;
  - a merge of two duplicate steps (rewritten feature-file lines and the removed step),
    with the same preview, confirm and changed-since-preview checks, and the same safety
    rules as the command line `merge`. All the files a merge touches are checked together;
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
  - a history of those changes, appended to `.gherkin-copilot/history.jsonl` inside the
    project. It holds the diffs, the scenario text before and after, and the name the user
    typed in. It never contains anything beyond what's already in the project's files. The app reads
  project folders the same way the command line does: with `ast`, never importing or
  running code.
- **Opens your browser** with Python's standard `webbrowser` module (use `--no-open` to
  skip).
- **Runs tests only when you ask, and only your own command.** A project can have a test
  command (for example `python -m pytest tests`), typed in by you in the project's
  details and stored in your `~/.gherkin-copilot/projects.json`. Until you set one, the
  app can't run anything. When you press Run, the app:
  - accepts only commands that run pytest (`pytest …` or `python -m pytest …`), optionally
    preceded by `NAME=value` environment settings;
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
- **Everything stays on your machine.** Screenshots, videos and traces are written to the local
  `reports/` folder and nothing is uploaded. The trace viewer command serves on `127.0.0.1` only.
- **Recordings can include whatever was on the page.** The demo shop shows no real data. If you
  point the suite at a real application, treat `reports/` as sensitive and don't share it
  outside your team. It is excluded from git by `.gitignore`.

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

## Dependencies

- `requirements.lock` pins **every** package (direct and transitive) to an exact version
  with **SHA-256 hashes** for every published file. Installing with `--require-hashes`
  makes pip refuse anything that has been altered or isn't on the list.
- Direct dependencies are kept to the minimum needed: `pytest`, `pytest-bdd`,
  `pytest-playwright` and `playwright`. Each is widely used and maintained, and listed in
  `requirements.in`.
- **Browsers:** Playwright can drive the Edge or Chrome already installed on the machine
  (`--browser-channel msedge` / `chrome`) or Brave (`--brave`), so nothing has to be
  downloaded. Downloading
  Playwright's own browser (`playwright install`) is optional.

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

## Checks run on this version

Results from 2026-10-12, after working through the independent review in
`reviews/` (see `gherkin-copilot-review-2026-10-11-status.md`):

| Check | Result |
|---|---|
| `pip-audit --strict -r requirements.lock` (known vulnerabilities) | No known vulnerabilities found |
| `bandit -q -r src demo run_demo.py` (static security analysis) | No findings. The three places that start a process (`run_demo.py` and the app's Run button in `ui/runner.py`) use fixed argument lists and no shell, and are annotated with why |
| `mypy --strict` | No issues in 19 source files |
| `ruff check` (rules in `pyproject.toml`) | No issues |
| Tool tests | 251 passed (macOS, Python 3.14), including tests for missing paths, malformed files, Windows code pages and generated inputs |
| Branch coverage (`coverage run -m pytest tests`) | 91% |
| Demo scenarios in a real browser (Brave) | 6 passed |
| `pip install --require-hashes -r requirements.lock` in a clean venv, then `pip check` | Installs cleanly, no broken requirements (checked 2026-10-09; the lock file hasn't changed since) |

The same checks run on every push in CI (`.github/workflows/ci.yml`), including the tests on
Windows. To repeat them yourself, see "Development" in the README.

**Error handling:** the command line tool turns bad input (missing paths, files that
aren't valid JSON or UTF-8) into a one-line message with exit code 2, and never prints a
traceback for it (OWASP ASVS V7.4). Unexpected internal errors exit with 3.

## Reporting a problem

If you find a security issue, please open a private report to the repository owner rather
than a public issue.
