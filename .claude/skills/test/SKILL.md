---
name: test
description: Run the MTFWBuilder test suites. Use before a commit, after a change, or when asked which tests to run.
allowed-tools: Bash, Read
---

# Test MTFWBuilder

Run these before every pull request:

1. `uv run pytest`. Unit, contract and integration tests.
2. `uv run ruff check mtfwbuilder/ tests/`.
3. `uv run ruff format --check mtfwbuilder/ tests/`.

Which suite covers what:
- Unit tests: generator, registry, build service, security.
- Contract tests: userPrefs keys and the registry against `tests/fixtures/firmware/`.
  Run them after any change to the generator or the registry.
- E2E smoke (Playwright, after Epic 3): run it after any change under `templates/` or
  `static/`. Install the browser once: `uv run python -m playwright install chromium`.
- Real build: manual only. It needs an installed firmware source and toolchains.

Rules:
- A test writes only under `tmp_path`. After `pytest`, `git status` must be clean.
- If a contract test fails after a fixture refresh, upstream changed. Read the upstream
  source before you change the generator.
