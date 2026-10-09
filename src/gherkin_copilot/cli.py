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
from dataclasses import asdict
from pathlib import Path

from gherkin_copilot import __version__
from gherkin_copilot.checker import check_steps, iter_feature_files, make_stub, parse_feature
from gherkin_copilot.indexer import StepDef, build_index, load_index, save_index
from gherkin_copilot.matcher import find, find_duplicates
from gherkin_copilot.merger import apply, plan_merges
from gherkin_copilot.report import write_report

DEFAULT_INDEX = Path(".gherkin-index.json")


def _load_steps(args: argparse.Namespace) -> list[StepDef]:
    """Use the saved index unless a path was given or no index exists yet."""
    if args.path is None and args.index.exists():
        return load_index(args.index)
    return build_index(Path(args.path or "."))


def cmd_index(args: argparse.Namespace) -> int:
    steps = build_index(Path(args.path or "."))
    if args.json:
        print(json.dumps([asdict(s) for s in steps], indent=2))
    else:
        for s in steps:
            print(f"{s.keyword.capitalize():<6} {s.pattern}")
            print(f"       {s.location}  ({s.kind}, {s.function})")
    if not args.no_save:
        save_index(steps, args.index)
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
            print(f"  ✓ {m.step.keyword.capitalize()} {m.step.pattern}   [{m.step.location}]")
    else:
        print("No step definition matches this text.")
    if fuzzy:
        print("\nDid you mean:" if not exact else "\nSimilar steps:")
        for m in fuzzy:
            print(f"  {m.score:.2f}  {m.step.keyword.capitalize()} {m.step.pattern}   [{m.step.location}]")
    return 0 if exact else 1


def cmd_dupes(args: argparse.Namespace) -> int:
    steps = _load_steps(args)
    pairs = find_duplicates(steps, threshold=args.threshold)
    if args.json:
        print(json.dumps([{"score": p.score, "first": asdict(p.first), "second": asdict(p.second)} for p in pairs], indent=2))
        return 0
    if not pairs:
        print(f"No likely duplicates among {len(steps)} step definitions.")
        return 0
    print(f"{len(pairs)} possible duplicate pair(s):\n")
    for p in pairs:
        print(f"  {p.score:.2f}  {p.first.keyword.capitalize()} {p.first.pattern}   [{p.first.location}]")
        print(f"        {p.second.keyword.capitalize()} {p.second.pattern}   [{p.second.location}]\n")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    steps = _load_steps(args)
    root = Path(args.features)
    base = root if root.is_dir() else root.parent
    results = []
    for feature in iter_feature_files(root):
        results.extend(check_steps(parse_feature(feature, str(feature.relative_to(base))), steps))

    if args.json:
        print(json.dumps([{"file": r.step.file, "line": r.step.line, "step": r.step.raw, "status": r.status,
                           "bound_to": [s.location for s in r.bound]} for r in results], indent=2))
    else:
        current = None
        for r in results:
            if r.step.file != current:
                current = r.step.file
                print(f"\n{current}")
            mark = {"ok": "✓", "missing": "✗", "ambiguous": "!"}[r.status]
            where = r.bound[0].location if r.status == "ok" else ""
            print(f"  {mark} {r.step.line:>3}  {r.step.raw}   {where}".rstrip())
            if r.status == "ambiguous":
                for s in r.bound:
                    print(f"           matches {s.keyword.capitalize()} {s.pattern}   [{s.location}]")
            for m in r.suggestions:
                print(f"           did you mean ({m.score:.2f}) {m.step.keyword.capitalize()} {m.step.pattern}   [{m.step.location}]")

        counts = {k: sum(r.status == k for r in results) for k in ("ok", "missing", "ambiguous")}
        print(f"\n{len(results)} steps: {counts['ok']} ok, {counts['missing']} missing, {counts['ambiguous']} ambiguous")

        missing = [r.step for r in results if r.status == "missing"]
        if args.stubs and missing:
            seen = set()
            print("\n# --- Stubs for missing steps (pytest-bdd) ---")
            print("from pytest_bdd import given, parsers, then, when\n")
            for step in missing:
                stub = make_stub(step)
                if stub not in seen:
                    seen.add(stub)
                    print(f"\n{stub}")
    return 0 if all(r.status == "ok" for r in results) else 1


def cmd_merge(args: argparse.Namespace) -> int:
    root = Path(args.path or ".")
    result = plan_merges(root, threshold=args.threshold, keep=frozenset(args.keep))
    written = apply(result) if args.apply else []

    if args.json:
        print(json.dumps({
            "applied": bool(args.apply),
            "merges": [{"score": p.score, "keep": asdict(p.keep), "drop": asdict(p.drop), "ok": p.ok,
                        "problems": p.problems,
                        "rewritten_lines": [{"file": str(e.file), "line": e.line, "old": e.old.strip(), "new": e.new.strip()}
                                            for e in p.edits]} for p in result.plans],
            "files_changed": [str(f) for f in result.changes],
        }, indent=2))
        return 0 if all(p.ok for p in result.plans) else 1

    if not result.plans:
        print("No likely duplicates to merge.")
        return 0
    base = Path.cwd()
    for p in result.plans:
        print(f"{'✓' if p.ok else '✗'} {p.score:.2f}  keep  {p.keep.keyword.capitalize()} {p.keep.pattern}   [{p.keep.location}]")
        print(f"        {'drop' if p.ok else '    '}  {p.drop.keyword.capitalize()} {p.drop.pattern}   [{p.drop.location}]")
        for problem in p.problems:
            print(f"        can't merge: {problem}")
        if p.ok:
            n = len(p.edits)
            print(f"        {n} feature line{'' if n == 1 else 's'} rewritten, {p.drop.function}() removed")
        print()
    for first, second in result.skipped:
        print(f"Skipped {first.function} / {second.function}: run merge again after this round.\n")

    for path, (old, new) in result.changes.items():
        shown = path.relative_to(base) if path.is_relative_to(base) else path
        sys.stdout.writelines(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{shown}", f"b/{shown}"))
    if written:
        print(f"\nUpdated {len(written)} file(s). Re-run `gherkin-copilot index` to refresh the saved index.")
    elif result.changes:
        print("\nPreview only, nothing was changed. Re-run with --apply to make these changes.")
    return 0 if all(p.ok for p in result.plans) else 1


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
    p.add_argument("-o", "--output", type=Path, default=Path("reports/report.html"), help="HTML file to write (default: %(default)s)")
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
