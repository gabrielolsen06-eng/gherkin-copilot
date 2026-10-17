---
name: update-dependencies
description: Use when adding, removing or upgrading a Python dependency or regenerating requirements.lock in gherkin-copilot. Covers the pip-compile flags, the colorama pin, the audits to re-run, and copying pins into the starter templates so tests/test_init.py passes.
---

# Updating dependencies

Edit `requirements.in`, then regenerate the lock with pip-tools (in a separate venv):
`pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in`.
Keep `colorama` pinned (pytest needs it on Windows). Run `pip-audit -r requirements.lock`
and `bandit -r src demo run_demo.py` afterwards. Then copy the lock to
`src/gherkin_copilot/starter/requirements.lock.tmpl` and the pins to `requirements.in.tmpl`
(the project `init` creates), and the Playwright image tag in `starter/gitlab-ci.yml.tmpl`
and `.github/workflows/ci.yml` if playwright changed; `tests/test_init.py` fails until all match.

`requirements-desktop.lock` is different: it's hand-written; update its hashes from PyPI's JSON,
never via pip-compile on macOS (it drops Windows-only packages).

Afterwards, update SECURITY.md's "Checks run" table (pip-audit result, date).
