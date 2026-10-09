# gherkin-copilot

Write Gherkin scenarios faster by reusing the steps you already have.

`gherkin-copilot` reads the step definitions in a Python BDD project (pytest-bdd or behave)
and helps you:

| Command | What it does |
|---|---|
| `index` | Lists every step definition with its file and line |
| `find "STEP"` | Says whether a line of Gherkin binds to a step, and suggests close matches if it doesn't |
| `dupes` | Spots step definitions that say the same thing in slightly different words |
| `check FEATURES` | Checks every line of your .feature files, and drafts Python code for missing steps (`--stubs`) |
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
├── features/       login.feature, cart.feature: 5 scenarios, all passing
├── drafts/         checkout.feature: a new scenario whose steps aren't written yet
├── steps/          step definitions shared by all features
├── tests/          collects the scenarios for pytest
├── conftest.py     starts the shop, browser options
├── browsers.py     finds Brave (or another Chromium browser) to drive
└── server.py       localhost-only static server with security headers
```

Two steps in `demo/steps` are deliberate near-duplicates, so `dupes` has something to find.

## Using it on your own project

```bash
gherkin-copilot index path/to/your/project          # saves .gherkin-index.json
gherkin-copilot find 'Given I open the login page'
gherkin-copilot dupes
gherkin-copilot check path/to/features --stubs
```

Example output from `check`:

```
checkout.feature
  ✓   7  Given I am logged in as "standard_user"   steps/login_steps.py:27
  ✓   8  When I add "Sauce Labs Backpack" to the cart   steps/cart_steps.py:11
  ✗   9  And I open the cart page
  ✗  12  Then the order total is "$29.99"

# --- Stubs for missing steps (pytest-bdd) ---
@then(parsers.parse('the order total is "{text1}"'))
def the_order_total_is(page, text1):
    raise NotImplementedError('Step not written yet: the order total is "$29.99"')
```

Every command accepts `--json`. `find` and `check` exit with code 1 when something
doesn't bind, so they can gate a pull request in CI or a pre-commit hook.

## How matching works

- **Exact:** `parsers.parse` / `cfparse` patterns (`{name}`, `{count:d}`) are converted to
  regular expressions; `parsers.re` patterns are used as written. `check` also respects the
  keyword: a `@when` step does not satisfy a `Given` line, while behave's `@step` matches any.
- **Fuzzy:** placeholders, quoted values and numbers become `<arg>`, filler words are dropped,
  and the result is scored by a blend of character similarity and word overlap (0 to 1).

Fuzzy matching compares wording, not meaning: "I log in as admin" and "the admin user is
signed in" won't be flagged as duplicates. That's a job for the planned LLM features.

## Development

```bash
python -m pytest tests       # tool tests (35)
python -m pytest demo        # browser scenarios (5)
```

Useful environment variables for the demo:

| Variable | Purpose |
|---|---|
| `DEMO_BASE_URL` | Run the scenarios against another site instead of the bundled shop |
| `DEMO_BROWSER` | Set to `brave` to find and use Brave (same as `run_demo.py --brave`) |
| `DEMO_BROWSER_PATH` | Path to any Chromium-based browser executable; overrides `DEMO_BROWSER` |

## Roadmap

1. ~~`index`, `find`, `dupes`~~
2. ~~`check` with stub generation, runnable demo~~
3. ~~HTML report, traces and videos~~
4. `generate STORY.txt`: draft scenarios from a user story with an LLM, using only existing steps
5. CI on GitHub Actions
