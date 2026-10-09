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
- **No shell commands.** The tool never spawns processes. `run_demo.py` runs Python
  subprocesses with argument lists, never through a shell.

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
    browser. The app reads
  project folders the same way the command line does: with `ast`, never importing or
  running code.
- **Opens your browser** with Python's standard `webbrowser` module (use `--no-open` to
  skip). It starts no other programs.

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

Results from 2026-10-09:

| Check | Result |
|---|---|
| `pip-audit --strict -r requirements.lock` (known vulnerabilities) | No known vulnerabilities found |
| `bandit -r src demo run_demo.py` (static security analysis) | No medium or high findings. Two low, informational notes say `run_demo.py` uses `subprocess`. It only re-runs the current Python with fixed argument lists and no shell. |
| `pip install --require-hashes -r requirements.lock` in a clean venv, then `pip check` | Installs cleanly, no broken requirements |
| Tool tests / demo scenarios | 84 of 85 passed / 5 passed. The failure is `test_brave_found_on_mac`, which finds the real Brave in `/Applications` before the test's fake one; it isn't related to the tool. `bandit` and `pip-audit` were not re-run after `merge` was added. |

To repeat these checks yourself, install `pip-audit` and `bandit` in a separate
virtual environment and run the commands above.

## Reporting a problem

If you find a security issue, please open a private report to the repository owner rather
than a public issue.
