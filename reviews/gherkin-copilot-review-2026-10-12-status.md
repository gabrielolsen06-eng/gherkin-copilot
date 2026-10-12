# Status of the 2026-10-12 re-review

The re-review ("gherkin-copilot: re-review after fixes", 2026-10-12, scored 8.2 / 10) was
written against commit `ac14430`. This file records what was done about its new findings
(N0–N5). Every code fix has a regression test in `tests/test_unhappy_paths.py`.

| # | Finding | Status |
|---|---|---|
| N0 | Generated `demo/steps/test_build.py`, `demo/features/test_build.feature` and a PowerPoint lock file committed; 12 tests fail, CI red | Fixed. All three removed from git, `~$*` ignored. Pre-commit is installed and now runs the tests too; the hooks call `.venv-dev/bin/…` and `.venv/bin/python` directly, so they also work from the VS Code commit button without an activated environment. |
| N1 | pytest-bdd plain-string steps with `{}` matched as parse patterns | Fixed. `matcher.to_regex` matches `string` steps literally (`re.escape`), and fuzzy matching no longer treats their braces as placeholders. behave files are unchanged: the indexer already marks their `{name}` strings as parse patterns. |
| N2 | Infinite, NaN or huge line numbers in a Cucumber report crash | Fixed. `report._number` returns 0 for anything that isn't a finite number. This also covers the app's results view (`ui/runner.py`), which uses the same loader. |
| N3 | Bad `created` value in an index gives a bare float error | Fixed. `load_index` reports any bad value as "isn't a valid index file". |
| N4 | `cli.main` catches KeyError / TypeError / ValueError around every command | Fixed. Only the loaders (index, report, feature files, merge planning) are wrapped by `cli._input_file()`, which turns their ValueError into a usage error (exit 2). Anything else is an internal error: traceback and exit 3. |
| N5 | OS-native paths in `merge --json`, the stale-index note and the merge diff; "1 step definitions" | Fixed. `.as_posix()` everywhere, and counts are pluralised properly. |

## Not done here

- **Licence (M5):** still open by choice, waiting on a check of the owner's employment contract.
- **`# language:` and Examples beyond the first row (L7):** documented limits, unchanged.

## Found by the first Windows CI run

The first push ran the tests on Windows for the first time. Three failed:

- **Real bug:** the app's Run button split the saved test command in shlex's non-POSIX mode on
  Windows, which keeps the quote marks (`-k "a b"` reached pytest as `"a b"`) and splits
  `--x="a b"` in two. `runner.split_command` now removes quotes on every platform and only
  treats backslashes differently (path separators on Windows). Tested for both platforms.
- **Test bugs:** the Brave test set `HOME`, which `Path.home()` ignores on Windows, and the
  projects test used `/no/such/folder`, which isn't an absolute path on Windows.

## Follow-ups

- **Stale index (M2):** now also notices a step file that was deleted or renamed since the index
  was made (a rename keeps the old mtime, so the time check alone missed it). No change to the
  index format: it already records which file each step came from.
- **CI:** `actions/checkout`, `setup-python` and `upload-artifact` moved to v7 (Node 24); GitHub
  deprecated the Node 20 versions.
