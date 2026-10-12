"""Command line interface.

    gherkin-copilot index [PATH]          list every step definition and save the index
    gherkin-copilot find "STEP TEXT"      exact and "did you mean" matches for a step
    gherkin-copilot dupes                 step definitions that look like duplicates
    gherkin-copilot merge [PATH]          merge duplicate steps (preview; --apply to write)
    gherkin-copilot check [FEATURES]      check .feature files bind to real steps, draft stubs
    gherkin-copilot report [JSON]         turn a Cucumber JSON report into an HTML page
    gherkin-copilot ui                    open the app in your browser (local only)
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from gherkin_copilot import __version__
from gherkin_copilot.checker import FeatureStep, StepResult, check_steps, iter_feature_files, make_stub, parse_feature
from gherkin_copilot.indexer import StepDef, build_index, iter_python_files, load_index, save_index
from gherkin_copilot.matcher import find, find_duplicates
from gherkin_copilot.merger import MergeResult, apply, plan_merges
from gherkin_copilot.report import write_report

DEFAULT_INDEX = Path(".gherkin-index.json")
MARKS = {"ok": "✓", "missing": "✗", "ambiguous": "!"}
ASCII_MARKS = {"ok": "OK", "missing": "MISSING", "ambiguous": "AMBIGUOUS"}


class UsageError(Exception):
    """A problem with what the user asked for: printed as one line, exit code 2."""


def _existing_path(text: str | None, what: str) -> Path:
    path = Path(text or ".")
    if not path.exists():
        raise UsageError(f"{what} not found: {path}")
    return path


def _load_steps(args: argparse.Namespace) -> list[StepDef]:
    """Use the saved index unless a path was given or no index exists yet."""
    if args.path is None and args.index.exists():
        index = load_index(args.index)
        newer = next((p for p in iter_python_files(index.root) if p.stat().st_mtime > index.created), None) \
            if index.root and index.root.is_dir() else None
        if newer:
            print(f"Note: {newer} changed after the saved index was made. Run `gherkin-copilot index` again, "
                  "or pass --path to scan fresh.", file=sys.stderr)
        return index.steps
    return build_index(_existing_path(args.path, "Folder"))


def _describe(step: StepDef) -> str:
    return f"{step.keyword.capitalize()} {step.pattern}   [{step.location}]"


def _mark(status: str) -> str:
    """✓ / ✗, or plain words when the output can't show them (e.g. a Windows log file)."""
    mark = MARKS[status]
    try:
        mark.encode(sys.stdout.encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return ASCII_MARKS[status]
    return mark


def cmd_index(args: argparse.Namespace) -> int:
    root = _existing_path(args.path, "Folder")
    steps = build_index(root)
    if args.json:
        print(json.dumps([asdict(s) for s in steps], indent=2))
    else:
        for s in steps:
            print(f"{s.keyword.capitalize():<6} {s.pattern}")
            print(f"       {s.location}  ({s.kind}, {s.function})")
    if not args.no_save:
        save_index(steps, args.index, root)
    print(f"\n{len(steps)} step definitions indexed", end="", file=sys.stderr)
    print("" if args.no_save else f", saved to {args.index}", file=sys.stderr)
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    steps = _load_steps(args)
    matches = find(args.text, steps, limit=args.limit, min_score=args.min_score)
    if args.json:
        print(json.dumps([{"score": m.score, "exact": m.exact, **asdict(m.step)} for m in matches], indent=2))
        return 0 if any(m.exact for m in matches) else 1

    exact = [m for m in matches if m.exact]
    fuzzy = [m for m in matches if not m.exact]
    if exact:
        print("Matches:")
        for m in exact:
            print(f"  {_mark('ok')} {_describe(m.step)}")
    else:
        print("No step definition matches this text.")
    if fuzzy:
        print("\nDid you mean:" if not exact else "\nSimilar steps:")
        for m in fuzzy:
            print(f"  {m.score:.2f}  {_describe(m.step)}")
    return 0 if exact else 1


def cmd_dupes(args: argparse.Namespace) -> int:
    steps = _load_steps(args)
    pairs = find_duplicates(steps, threshold=args.threshold)
    if args.json:
        print(json.dumps([{"score": p.score, "first": asdict(p.first), "second": asdict(p.second)} for p in pairs],
                         indent=2))
        return 0
    if not pairs:
        print(f"No likely duplicates among {len(steps)} step definitions.")
        return 0
    print(f"{len(pairs)} possible duplicate pair(s):\n")
    for p in pairs:
        print(f"  {p.score:.2f}  {_describe(p.first)}")
        print(f"        {_describe(p.second)}\n")
    return 0


def _print_results(results: list[StepResult]) -> None:
    current = None
    for r in results:
        if r.step.file != current:
            current = r.step.file
            print(f"\n{current}")
        where = r.bound[0].location if r.status == "ok" else ""
        print(f"  {_mark(r.status)} {r.step.line:>3}  {r.step.raw}   {where}".rstrip())
        if r.status == "ambiguous":
            for s in r.bound:
                print(f"           matches {_describe(s)}")
        for m in r.suggestions:
            print(f"           did you mean ({m.score:.2f}) {_describe(m.step)}")
    counts = {k: sum(r.status == k for r in results) for k in ("ok", "missing", "ambiguous")}
    print(f"\n{len(results)} steps: {counts['ok']} ok, {counts['missing']} missing, {counts['ambiguous']} ambiguous")


def _print_stubs(missing: list[FeatureStep]) -> None:
    print("\n# --- Stubs for missing steps (pytest-bdd) ---")
    print("from pytest_bdd import given, parsers, then, when\n")
    for stub in dict.fromkeys(make_stub(step) for step in missing):
        print(f"\n{stub}")


def cmd_check(args: argparse.Namespace) -> int:
    root = _existing_path(args.features, "Feature file or folder")
    steps = _load_steps(args)
    base = root if root.is_dir() else root.parent
    features = list(iter_feature_files(root))
    if not features:
        raise UsageError(f"No .feature files found in {root}")
    results = []
    for feature in features:
        results.extend(check_steps(parse_feature(feature, feature.relative_to(base).as_posix()), steps))

    if args.json:
        print(json.dumps([{"file": r.step.file, "line": r.step.line, "step": r.step.raw, "status": r.status,
                           "bound_to": [s.location for s in r.bound]} for r in results], indent=2))
    else:
        _print_results(results)
        missing = [r.step for r in results if r.status == "missing"]
        if args.stubs and missing:
            _print_stubs(missing)
    return 0 if all(r.status == "ok" for r in results) else 1


def _merge_json(result: MergeResult, applied: bool) -> str:
    return json.dumps({
        "applied": applied,
        "merges": [{"score": p.score, "keep": asdict(p.keep), "drop": asdict(p.drop), "ok": p.ok,
                    "problems": p.problems,
                    "rewritten_lines": [{"file": str(e.file), "line": e.line,
                                         "old": e.old.strip(), "new": e.new.strip()} for e in p.edits]}
                   for p in result.plans],
        "files_changed": [str(f) for f in result.changes],
    }, indent=2)


def _print_merge_plans(result: MergeResult) -> None:
    for p in result.plans:
        print(f"{_mark('ok' if p.ok else 'missing')} {p.score:.2f}  keep  {_describe(p.keep)}")
        print(f"        {'drop' if p.ok else '    '}  {_describe(p.drop)}")
        for problem in p.problems:
            print(f"        can't merge: {problem}")
        if p.ok:
            n = len(p.edits)
            print(f"        {n} feature line{'' if n == 1 else 's'} rewritten, {p.drop.function}() removed")
        print()
    for first, second in result.skipped:
        print(f"Skipped {first.function} / {second.function}: run merge again after this round.\n")


def _print_merge_diff(result: MergeResult) -> None:
    base = Path.cwd()
    for path, (old, new) in result.changes.items():
        shown = path.relative_to(base) if path.is_relative_to(base) else path
        sys.stdout.writelines(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{shown}", f"b/{shown}"))


def cmd_merge(args: argparse.Namespace) -> int:
    root = _existing_path(args.path, "Folder")
    features = _existing_path(args.features, "Features folder") if args.features else None
    result = plan_merges(root, threshold=args.threshold, keep=frozenset(args.keep), features=features)
    written = apply(result) if args.apply else []
    code = 0 if all(p.ok for p in result.plans) else 1
    if args.json:
        print(_merge_json(result, bool(args.apply)))
        return code
    if not result.plans:
        print("No likely duplicates to merge.")
        return 0
    _print_merge_plans(result)
    _print_merge_diff(result)
    if written:
        print(f"\nUpdated {len(written)} file(s). Re-run `gherkin-copilot index` to refresh the saved index.")
    elif result.changes:
        print("\nPreview only, nothing was changed. Re-run with --apply to make these changes.")
    return code


def cmd_report(args: argparse.Namespace) -> int:
    if not args.json_report.is_file():
        print(f"No report found at {args.json_report}. Run pytest with --cucumberjson first.", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    features = write_report(args.json_report, args.output, args.artifacts)
    scenarios = [s for f in features for s in f.scenarios]
    passed = sum(s.status == "passed" for s in scenarios)
    print(f"{passed}/{len(scenarios)} scenarios passed. Report: {args.output}")
    if args.open:
        import webbrowser

        webbrowser.open(args.output.resolve().as_uri())
    return 0 if passed == len(scenarios) else 1


def cmd_ui(args: argparse.Namespace) -> int:
    from gherkin_copilot.ui.projects import DEFAULT_FILE, ProjectStore
    from gherkin_copilot.ui.server import make_server

    store = ProjectStore(args.projects or DEFAULT_FILE)
    try:
        server, _ = make_server(store, args.port)
    except OSError:  # port taken: let the system pick a free one
        server, _ = make_server(store, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"gherkin-copilot is running at {url}  (only reachable from this computer; Ctrl+C to stop)")
    print(f"Projects are saved in {store.path}")
    if not args.no_open:
        import webbrowser

        webbrowser.open(url)
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gherkin-copilot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="index file (default: %(default)s)")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("index", help="scan step definitions and save an index")
    p.add_argument("path", nargs="?", help="project folder or file to scan (default: .)")
    p.add_argument("--no-save", action="store_true", help="print only, don't write the index file")
    common(p)
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("find", help="look up step text against the index")
    p.add_argument("text", help='step text, e.g. "Given I log in as \\"admin\\""')
    p.add_argument("--path", help="scan this folder instead of using the saved index")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--min-score", type=float, default=0.4, help="hide suggestions below this score")
    common(p)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("dupes", help="report step definitions with near-identical wording")
    p.add_argument("--path", help="scan this folder instead of using the saved index")
    p.add_argument("--threshold", type=float, default=0.6, help="similarity needed to report a pair")
    common(p)
    p.set_defaults(func=cmd_dupes)

    p = sub.add_parser("merge", help="merge duplicate step definitions (preview by default)")
    p.add_argument("path", nargs="?", help="project folder with step definitions and .feature files (default: .)")
    p.add_argument("--threshold", type=float, default=0.6, help="similarity needed to treat a pair as duplicates")
    p.add_argument("--keep", action="append", default=[], metavar="FUNCTION",
                   help="prefer keeping this step function (can be repeated)")
    p.add_argument("--features", help="folder with the .feature files, if it isn't inside PATH")
    p.add_argument("--apply", action="store_true", help="write the changes instead of only showing them")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("check", help="check that every step in .feature files has a definition")
    p.add_argument("features", nargs="?", default=".", help="feature file or folder (default: .)")
    p.add_argument("--path", help="scan this folder for step definitions instead of using the saved index")
    p.add_argument("--stubs", action="store_true", help="print pytest-bdd stubs for missing steps")
    common(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("report", help="turn a Cucumber JSON report into a self-contained HTML page")
    p.add_argument("json_report", nargs="?", type=Path, default=Path("reports/cucumber.json"),
                   help="Cucumber JSON file (default: %(default)s)")
    p.add_argument("-o", "--output", type=Path, default=Path("reports/report.html"),
                   help="HTML file to write (default: %(default)s)")
    p.add_argument("--artifacts", type=Path, default=Path("reports/artifacts"),
                   help="pytest-playwright --output folder with screenshots, videos and traces (default: %(default)s)")
    p.add_argument("--open", action="store_true", help="open the report in your default browser")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("ui", help="open the gherkin-copilot app in your browser (runs on this computer only)")
    p.add_argument("--port", type=int, default=8765, help="port to listen on (default: %(default)s)")
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.add_argument("--projects", type=Path, help="project list file (default: ~/.gherkin-copilot/projects.json)")
    p.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # never crash on characters a console or log file can't show
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    command: Callable[[argparse.Namespace], int] = args.func
    try:
        return command(args)
    except UsageError as exc:
        message = str(exc)
    except OSError as exc:
        message = f"{exc.strerror or exc}: {exc.filename}" if exc.filename else str(exc)
    except (ValueError, KeyError, TypeError) as exc:  # malformed index / report / feature files
        message = str(exc) or type(exc).__name__
    except Exception:  # a bug: show the details, and an exit code CI can tell apart from findings (1)
        traceback.print_exc()
        print("gherkin-copilot: internal error, please report it with the details above", file=sys.stderr)
        return 3
    print(f"gherkin-copilot: error: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
