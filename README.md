# gherkin-copilot

Write Gherkin scenarios faster by reusing the steps you already have.

`gherkin-copilot` reads the step definitions in a Python BDD project (pytest-bdd or behave)
and helps you:

| Command | What it does |
|---|---|
| `init [FOLDER]` | Starts a new BDD project: pytest-bdd + Playwright, a shared step library, an example that runs offline, and the conventions in its README |
| `index` | Lists every step definition with its file and line |
| `find "STEP"` | Says whether a line of Gherkin binds to a step, and suggests close matches if it doesn't |
| `dupes` | Spots step definitions that say the same thing in slightly different words |
| `merge` | Merges those duplicates: rewrites feature files to use one wording and deletes the other step (preview first, `--apply` to write) |
| `check FEATURES` | Checks every line of your .feature files, and drafts Python code for missing steps (`--stubs`) |
| `ui` | Opens the gherkin-copilot app in your browser: build and edit scenarios with blocks, run them, browse every step, merge duplicates, see the history of changes |
| `report [JSON]` | Turns a Cucumber JSON results file into a single HTML page, with screenshots, videos and traces linked |

The tool reads source code with Python's `ast` module. It never imports or runs your
code, has **no third-party dependencies**, and makes **no network calls**. See
[SECURITY.md](SECURITY.md) for the details a security team will ask about.

## Quick start (full demo)

You need Python 3.11 or newer. These commands work the same on Windows, macOS and Linux
(on Windows use `.venv\Scripts\activate` instead of `source .venv/bin/activate`).

```bash
cd gherkin-copilot
python -m venv .venv
source .venv/bin/activate

# Install the exact, hash-checked versions of the demo's dependencies
python -m pip install --require-hashes -r requirements.lock
# Install gherkin-copilot itself (no extra downloads)
python -m pip install --no-deps --no-build-isolation .

# Run the whole demo, using a browser that is already installed:
python run_demo.py --brave                    # Brave
python run_demo.py --browser-channel chrome   # Chrome
python run_demo.py --browser-channel msedge   # Edge
```

On a Mac, use `python3` for the first `venv` command if `python` isn't found.

**Brave** isn't a browser Playwright knows by name, but it's built on Chromium, so
`--brave` finds it in its standard install location and drives it like Chrome
(`/Applications/Brave Browser.app` on macOS, `Program Files\BraveSoftware` on Windows,
`brave-browser` on Linux). If Brave lives somewhere else, set `DEMO_BROWSER_PATH` to its
executable instead.

`run_demo.py` walks through every command and then runs the BDD scenarios in a real
browser. Add `--headed` to watch the browser click through the shop.

If your laptop allows downloading Playwright's own browser, you can run
`python -m playwright install chromium` once and then just `python run_demo.py`.

## Reviewing results

Every demo run writes to `reports/`:

| File | What it's for |
|---|---|
| `report.html` | One page with every feature, scenario and step: pass/fail, timings, errors, plus a screenshot per scenario (and a video with `--videos`, see below). Opens automatically when the run ends (add `--no-open` to skip). Failed scenarios are expanded; passed ones are collapsed. |
| `artifacts/<test>/trace.zip` | A Playwright trace: replay a scenario action by action with DOM snapshots, console and network. |
| `cucumber.json` | Standard Cucumber JSON, readable by other report tools and CI systems. |

Videos are off by default: Playwright needs its own copy of ffmpeg to record them, which
is an extra download (`python -m playwright install ffmpeg`) that a locked-down laptop may
block. If you can install it, add `--videos` to `run_demo.py`. Traces don't need it and
already show a frame-by-frame replay.

To replay a trace, run the command shown under the scenario in the report, for example:

```bash
python -m playwright show-trace --host 127.0.0.1 --port 0 "reports/artifacts/<test>/trace.zip"
```

This serves the trace viewer on your own machine and opens it in your default browser (Brave
works), so no extra browser download is needed.

You can build the HTML report from any Cucumber JSON file, including ones from your own
projects or other Cucumber tools:

```bash
gherkin-copilot report path/to/cucumber.json -o report.html --artifacts path/to/playwright/output --open
```

## What the demo shows

The `demo/` folder is a small pytest-bdd + Playwright suite for a **practice web shop that
ships inside this project** (`demo/app`). The tests start it on `127.0.0.1` automatically,
so the demo needs no internet access and touches no real system. Its page structure
mirrors [saucedemo.com](https://www.saucedemo.com), the well-known public practice site, so
the same suite can also be pointed there:

```bash
DEMO_BASE_URL=https://www.saucedemo.com python run_demo.py --browser-channel msedge
```

(Saucedemo's own wording for some error messages may differ from the bundled shop.)

To look at the shop yourself: `python demo/server.py`, then open http://127.0.0.1:8000.

```
demo/
├── app/            the practice shop (static HTML/JS/CSS, no external resources)
├── features/       login.feature, cart.feature: 6 scenarios, all passing
├── drafts/         checkout.feature: a new scenario whose steps aren't written yet
├── steps/          step definitions shared by all features
├── tests/          collects the scenarios for pytest
├── conftest.py     starts the shop, browser options
├── browsers.py     finds Brave (or another Chromium browser) to drive
└── server.py       localhost-only static server with security headers

demo/ is a Python package, so its modules import as demo.server, demo.browsers and
demo.steps.*. conftest.py loads every file in steps/, and tests/test_features.py runs every
feature file, so new steps and features are picked up without registering them.
login.feature shows a declarative scenario ("Given I am a registered customer / When I sign
in") next to imperative ones that spell out each value.
```

Two steps in `demo/steps` are deliberate near-duplicates, so `dupes` has something to find,
and `cart.feature` uses one of them so `merge` has a line to rewrite.

## Starting a new project

```bash
gherkin-copilot init payments-tests           # or --dry-run to see the list first
gherkin-copilot init payments-tests --ci gitlab   # also add a GitLab pipeline
gherkin-copilot init billing-tests --desktop    # a Windows desktop app instead of a website (see below)
```

This creates a ready-to-run pytest-bdd + Playwright project:

```
payments-tests/
  README.md              setup, running, adding scenarios, conventions, CI commands
  features/example.feature
  steps/                 the shared step library (every file here is loaded automatically)
    example_steps.py
    navigation_steps.py
  tests/test_features.py turns every scenario in features/ into a test
  conftest.py            loads steps/; BROWSER_PATH picks any Chromium-based browser
  pytest.ini             base_url for the application under test
  requirements.in / requirements.lock   pinned, hash-checked dependencies (same as this repo's)
  .gitignore
```

The example scenario needs no website and no network: the test writes its own page. It
proves Python, pytest-bdd, Playwright and the browser work on that machine, which is the
first thing to check on a locked-down laptop (`python -m pytest --browser-channel msedge`
uses the Edge that's already installed). `init` only ever adds files: anything that already
exists is kept and listed.

With `--ci gitlab` it also adds a `.gitlab-ci.yml` that runs every scenario on each push in
Playwright's Docker image (browsers included, so the pipeline downloads none), installs from
the hash-checked lock (through Artifactory if `PIP_INDEX_URL` is set as a CI/CD variable),
and shows the results in GitLab's Tests tab, keeping screenshots and traces of failures. This
repo's CI runs that file's commands in the same image on every push.

## Using it on your own project

```bash
gherkin-copilot index path/to/your/project          # saves .gherkin-index.json
gherkin-copilot find 'Given I open the login page'
gherkin-copilot dupes
gherkin-copilot merge path/to/your/project          # preview; add --apply to write
gherkin-copilot check path/to/features --stubs
```

Example output from `check`:

```
checkout.feature
  ✓   7  Given I am logged in as "standard_user"   steps/login_steps.py:26
  ✓   8  When I add "Sauce Labs Backpack" to the cart   steps/cart_steps.py:11
  ✓   9  And I open the cart page   steps/cart_steps.py:38
  ✗  10  And I go to checkout
  ✗  11  And I enter my details "Ada" "Lovelace" "90210"
  ✗  12  Then the order total is "$29.99"

6 steps: 3 ok, 3 missing, 0 ambiguous

# --- Stubs for missing steps (pytest-bdd) ---
@then(parsers.parse('the order total is "{text1}"'))
def the_order_total_is(page, text1):
    raise NotImplementedError('Step not written yet: the order total is "$29.99"')
```

### Merging duplicates

`merge` takes each pair that `dupes` reports and keeps one step: the one used in more
feature lines, or the one defined first on a tie (`--keep FUNCTION` to choose). Feature
lines that use the other wording are rewritten, values included, and the other step's
function is deleted:

```
✓ 0.64  keep  When I add "{product}" to the cart   [steps/cart_steps.py:11]
        drop  When I put the "{product}" in my cart   [steps/cart_steps.py:16]
        1 feature line rewritten, put_in_cart() removed

--- a/demo/features/cart.feature
+++ b/demo/features/cart.feature
-    When I put the "Sauce Labs Onesie" in my cart
+    When I add "Sauce Labs Onesie" to the cart
```

By default it only shows the diff. `--apply` writes it. A pair is left alone, with the
reason shown, unless merging can't change what the tests do: both steps must use the same
keyword and placeholders, their decorators must have the same options (such as
`target_fixture`), their functions must have identical parameters and code, the
removed function mustn't be called from anywhere else, and every rewritten line must bind
to the kept step. Commit first so the change is easy to review and undo.

Every command accepts `--json`, and exit codes are meant for CI:

| Exit code | Meaning |
|---|---|
| 0 | All good |
| 1 | Something doesn't bind (`find`, `check`) or can't be merged (`merge`): a finding |
| 2 | A problem with the input: a path that doesn't exist, no `.feature` files found, a file that isn't a valid index / report / UTF-8 text. One line on stderr, no traceback |
| 3 | An internal error (a bug): the details are printed so it can be reported |

So a typo in a path fails a CI gate instead of passing it. Output never crashes on a
Windows console or log file: the ✓ / ✗ marks become `OK` / `MISSING` when the output
can't show them.

The saved index (`.gherkin-index.json`) remembers which folder it came from and when. If a
step file is newer, `find`, `dupes` and `check` say so; re-run `index` or pass `--path`.

**Framework detection:** in a file that imports `pytest_bdd`, a plain-string step such as
`@given("the payload is {}")` is matched literally, as pytest-bdd does. In behave files a
plain string with `{name}` is a parse pattern, as behave does.

**Limits of the feature-file reader:** `# language:` (non-English Gherkin keywords) isn't
supported, and for a Scenario Outline only the first Examples row is used to check that
each line binds.

## The app

```bash
gherkin-copilot ui
```

This starts the app on your own computer (http://127.0.0.1:8765, never reachable from the
network) and opens it in your browser. Add a project by giving it a name, the folder with
your step definitions, and optionally the folder with your `.feature` files. You can add
as many projects as you like and switch between them at the top; the list is saved in
`~/.gherkin-copilot/projects.json`.

**Home** is the project at a glance, and where the app opens:

- **Numbers:** scenarios, step definitions (used and unused), how often each step is
  reused, feature lines with no step behind them, duplicate pairs and the last test run.
  Click one to go to that screen.
- **Latest commit:** the branch, the last commit (message, author, when), whether you have
  commits to push or pull (as of your last fetch), when you last pulled and fetched, how
  many commits were made in the last 7 days, and recent activity in your copy (commits,
  pulls, merges, branch switches). The app reads this from the project's `.git` folder
  itself, so it works without running git.
- **Most used steps** with how many feature-file lines use each, **Unused steps** (no
  feature file uses them) with **Open in VS Code**, the latest **changes made in the app**,
  and **Tags** with how many scenarios each covers (click one to list them).

**Build a scenario** is a block editor, a bit like Scratch. Every step the project already
has is a block on the left: drag it into your scenario (or click **+**) and fill in its
blanks, such as the product name. Blocks can only be made from steps that exist, so they
are ready to run by construction. You can also type a line by hand; each line is checked as
you type and shows:

- ✓ when it matches an existing step,
- ✗ when it doesn't, with “did you mean” suggestions (click one to swap it in) and the
  drafted code for a new step,
- ! when more than one step matches, or a value doesn't fit (e.g. text in a number blank).

The Gherkin text builds up live on the right. When you're done:

- **Save to a feature file** creates a new `.feature` file or adds the scenario to an
  existing one.
- **Add missing steps to code** adds templates for the steps that don't exist yet to one of
  your step files (with any imports they need). They fail with “Step not written yet” until
  someone writes the body.

- **Build its code with action blocks** (on any line that doesn't exist yet) writes the real
  code for the new step, no Python needed. Name the parts of the line that change (e.g.
  `"Ada"` → `first_name`), then add actions in order: go to a page, click, type into,
  choose an option, tick a checkbox, press a key, check text / visibility / how many / the
  address. Find elements the way a user sees them (a button's text, a field's label) or by
  CSS selector. The Playwright code updates live, and saves into an existing or a new step
  file, so the step runs straight away.

All of these show exactly what will change and only write when you confirm. If the file changed
in the meantime, saving is refused so nothing is overwritten. Your draft is kept in the
browser per project, so a reload doesn't lose it.

**Scenarios** lists every feature file and its scenarios, with tags, step counts and
whether every line has a step behind it. From there you can:

- **Edit** a scenario: it opens in the block editor, with each line turned back into a
  block and its values filled in (lines with no step behind them open as text). Change the
  steps, the name, the tags or the description; Scenario Outlines get a table editor for
  their Examples, and the Background can be edited too. Data tables and doc strings under a
  step are kept as they are. **Save changes** replaces just that scenario in the file;
  **Save as a new scenario** keeps the original.
- **Duplicate** a scenario as a starting point for a similar one, or **Delete** it.

**History** lists every change made through the app: what was done, to which scenario,
by whom and when, with the before/after diff. Changes to a scenario (edits, deletions,
additions) can be undone from here, with the usual preview. The history is stored in the
project, in `.gherkin-copilot/history.jsonl`, so committing it shares it with the team.
The app asks for your name the first time you save something.

The **Steps** screen lists every step in the selected project, grouped by Given / When /
Then, with placeholders such as “product” highlighted. Search it, or click a step to copy
it. If a step's code needs fixing, **Open in VS Code** opens its file at the right line. The
app rescans the project whenever you come back to the browser tab, so edits show up
straight away.

**Duplicates** shows each pair of steps that say the same thing in different words, side by
side: which one would be kept, which removed, and how many feature-file lines use each
(**Open in VS Code** on either side shows its code).
Click **Keep this one instead** to swap them, and **Preview merge** to see every file the
merge would change before confirming. Pairs that can't be merged safely say why. If the
only reason is that their code differs, **Compare their code** shows both functions side by
side with the differing lines highlighted, and **Merge anyway** merges them after the same
preview. From then on only the kept function's code runs, so run the tests afterwards. Other
reasons (the function is called or imported elsewhere, values can't be carried across) still
block the merge. The slider sets how alike two steps must be to be listed.

**Test results** runs your tests and shows what happened, scenario by scenario and step by
step: errors, timings, a screenshot of each scenario and a command to replay it in
Playwright's trace viewer. Run everything, one feature file (**▶ Run** on the Scenarios
tab) or the scenario you're editing (**▶ Run this scenario**), so you find out straight
away whether a change works. The ✓ in the editor only means a step *exists*; running the
scenario tells you whether it *passes*.

To turn this on, open **Details** and enter the command you'd use in a terminal, for
example:

| Project | Test command | Run from folder |
|---|---|---|
| This repository's demo | `DEMO_BROWSER=brave python -m pytest demo` | the `gherkin-copilot` folder |
| A typical project | `python -m pytest tests` | the project folder |

The app only runs pytest commands, never through a shell, and only when you press Run.
Results go to `.gherkin-copilot/results` in the project, which git ignores
automatically. To run a single scenario or file, its feature file must be loaded by a test
module (for example `scenarios("checkout.feature")` in a `test_*.py` file).

When demoing on this repository's `demo` folder, reset it afterwards with
`git checkout -- demo` (and delete any new `.feature` files you created). The demo's
history file is ignored by git here.

Options: `--port` to use another port, `--no-open` to not open the browser, `--projects`
to use a different project list file.

If your `.feature` files live outside the folder with your step definitions, the command
line `merge` takes the same split: `gherkin-copilot merge path/to/steps --features path/to/features`.

## Testing Windows desktop apps

The same plain-English scenarios can drive a Windows program instead of a website. Steps
press buttons, fill fields and read text through **UI Automation**, the accessibility
interface built into Windows (what screen readers use), so there's no extra driver and it
works on a locked screen. The only extra package is `comtypes` (pure Python), pinned in
`requirements-desktop.lock`.

```gherkin
Given the app is open
When I type "ACME Ltd" into "Customer name"
And I click "Save"
Then I see "Customer saved"
```

Start a desktop test project with:

```bash
gherkin-copilot init my-app-tests --desktop
```

It's ready to run on Windows (a test VM, not a machine with real work open), and its README
covers the rest:

```powershell
cd my-app-tests
python -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock -r requirements-desktop.lock
.venv\Scripts\python -m pytest          # the example tests Character Map, which every Windows has
```

Point it at your app in `pytest.ini`, like `base_url` for a website:

| Setting | Purpose |
|---|---|
| `desktop_app` | Path to the `.exe` to test. `DESKTOP_APP` overrides it for one run |
| `desktop_window` | Part of its main window's title. Only needed if it starts through a launcher or splash screen |
| `desktop_workdir` | Folder to start it in (default: the `.exe`'s own folder) |

Steps: `the app is open`, `I click "…"`, `I tick "…"`, `I type "…" into "…"`,
`"…" shows "…"`, `"…" is not empty`, `"…" is ticked`, `I see "…"`. Controls are found by the
name a screen reader reads out (a trailing colon or `&` shortcut marker doesn't matter), or by
the developer's AutomationId. To check an app can be tested this way, start Narrator
(Win + Ctrl + Enter) and Tab through it: if Narrator reads the names, the steps can use them.

Each scenario starts the app, and closes it afterwards even if the scenario fails. If the app
is already open, the scenario stops rather than take over (and later close) that window. Apps
that need administrator rights need the tests started as administrator. On macOS and Linux the
scenarios are skipped.

**In the app:** add the project like any other. A project whose steps use the desktop helper
gets desktop **action blocks** (click, tick, type into, check what a field shows, check text
appears), and **Add missing steps to code** writes steps that take the app instead of a
browser page; so does `check --stubs`.

`desktop_demo/` in this repository is the project `init --desktop` creates (a test keeps the
two identical), and CI runs it against Character Map on Windows.

## How matching works

- **Exact:** `parsers.parse` / `cfparse` patterns (`{name}`, `{count:d}`) are converted to
  regular expressions; `parsers.re` patterns are used as written. `check` also respects the
  keyword: a `@when` step does not satisfy a `Given` line, while behave's `@step` matches any.
- **Fuzzy:** placeholders, quoted values and numbers become `<arg>`, filler words are dropped,
  and the result is scored by a blend of character similarity and word overlap (0 to 1).

Fuzzy matching compares wording, not meaning: "I log in as admin" and "the admin user is
signed in" won't be flagged as duplicates.

## Development

```bash
python -m pytest tests       # tool tests (277)
python -m pytest demo        # browser scenarios (6)
python -m pytest desktop_demo  # desktop scenarios (3, Windows only)
```

Linting, type checking and security scans use dev-only tools, kept out of the project's
environment so the tool itself stays standard-library only:

```bash
python -m venv .venv-dev && .venv-dev/bin/pip install -r requirements-dev.txt
.venv-dev/bin/ruff check src tests demo desktop_demo run_demo.py     # lint (rules in pyproject.toml)
.venv-dev/bin/mypy                                       # strict type checking
.venv-dev/bin/bandit -q -r src demo desktop_demo run_demo.py          # security lint
.venv-dev/bin/pip-audit --strict -r requirements.lock -r requirements-desktop.lock   # known vulnerabilities
.venv-dev/bin/pre-commit install                         # run ruff, mypy, bandit and the tests on every commit
```

`.github/workflows/ci.yml` runs all of these plus the tests on Ubuntu and Windows
(Python 3.11 and 3.13), the browser demo, and the desktop scenarios on Windows on every push. It also creates a project with `init`
and runs it in Chromium, and on Windows in the installed Edge with no browser download, set up
exactly as that project's README says.

Useful environment variables for the demo:

| Variable | Purpose |
|---|---|
| `DEMO_BASE_URL` | Run the scenarios against another site instead of the bundled shop |
| `DEMO_BROWSER` | Set to `brave` to find and use Brave (same as `run_demo.py --brave`) |
| `DEMO_BROWSER_PATH` | Path to any Chromium-based browser executable; overrides `DEMO_BROWSER` |

## Roadmap

1. ~~`index`, `find`, `dupes`~~, ~~`merge`~~
2. ~~`check` with stub generation, runnable demo~~
3. ~~HTML report, traces and videos~~
4. ~~CI on GitHub Actions~~

No LLM features are planned: the tool is built to run fully offline on a locked-down, low-powered laptop.
