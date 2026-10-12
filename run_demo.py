"""Run the full gherkin-copilot demo, step by step.

    python run_demo.py                          # bundled demo shop, Playwright's Chromium
    python run_demo.py --browser-channel msedge # use the Edge already installed on this PC
    python run_demo.py --browser-channel chrome --headed
    python run_demo.py --brave --headed         # use Brave

Any extra arguments are passed straight to pytest / pytest-playwright.
Results go to ./reports: report.html (opens automatically, add --no-open to
skip), cucumber.json, and a trace and screenshot for every scenario.

    python run_demo.py --brave --videos         # also record a video per scenario

Videos are off by default because Playwright needs its own ffmpeg to record
them (a one-off `python -m playwright install ffmpeg`), which locked-down
machines may not allow. Traces already include a frame-by-frame replay.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = [sys.executable, "-m", "gherkin_copilot"]

STEPS = [
    ("Index every step definition in the demo suite", [*TOOL, "index", "demo", "--no-save"], False),
    ("Look up a step that exists",
     [*TOOL, "find", 'When I add "Sauce Labs Onesie" to the cart', "--path", "demo"], False),
    ("Look up a step worded differently", [*TOOL, "find", "Given I open the login page", "--path", "demo"], True),
    ("Find step definitions that duplicate each other", [*TOOL, "dupes", "--path", "demo"], False),
    ("Preview merging those duplicates (add --apply to write it)", [*TOOL, "merge", "demo"], False),
    ("Check the finished feature files", [*TOOL, "check", "demo/features", "--path", "demo"], False),
    ("Check a draft feature and draft code for its missing steps",
     [*TOOL, "check", "demo/drafts", "--path", "demo", "--stubs"], True),
]


def run(title: str, command: list[str], failure_expected: bool) -> bool:
    """Run one demo step. An expected failure must be exit code 1 ("found something"), not a crash."""
    shown = ["gherkin-copilot", *command[3:]] if command[:3] == TOOL else ["pytest", *command[3:]]
    print(f"\n{'=' * 72}\n{title}\n$ {' '.join(shown)}\n{'=' * 72}", flush=True)
    code = subprocess.run(command, cwd=ROOT, check=False).returncode  # list args, no shell
    if code == 1 and failure_expected:
        print("(exit code 1 is expected here: it's how CI would catch this)")
        return True
    if code not in (0, 1):
        print(f"(exit code {code}: the command failed, see the message above)")
    return code == 0


def main() -> int:
    extra = sys.argv[1:]
    open_report = "--no-open" not in extra
    if not open_report:
        extra.remove("--no-open")
    videos = "--videos" in extra
    if videos:
        extra.remove("--videos")
    if "--brave" in extra:  # Brave isn't a Playwright channel; conftest finds its executable
        extra.remove("--brave")
        os.environ["DEMO_BROWSER"] = "brave"
    # a list, not a generator: every step must run even after one fails, so all() can't short-circuit
    ok = all([run(*step) for step in STEPS])
    (ROOT / "reports").mkdir(exist_ok=True)
    pytest_cmd = [
        sys.executable, "-m", "pytest", "demo", "-v",
        "--cucumberjson=reports/cucumber.json",
        "--output=reports/artifacts", "--screenshot=on", "--tracing=on",
        f"--video={'on' if videos else 'off'}",
        *extra,
    ]
    ok = run("Run the BDD scenarios in a real browser", pytest_cmd, False) and ok
    report_cmd = [*TOOL, "report", "reports/cucumber.json", "-o", "reports/report.html"]
    run("Build the HTML report", [*report_cmd, *(["--open"] if open_report else [])], not ok)
    print(f"\nDemo {'finished successfully' if ok else 'had failures'}. Review the results in reports/report.html")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
