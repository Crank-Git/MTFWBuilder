# MTFWBuilder

MTFWBuilder is a self-hosted web application. An operator fills in a config form. The
project writes a `userPrefs.jsonc` file and compiles Meshtastic firmware with PlatformIO.
The operator downloads the artifact and flashes the device. Every userPrefs key is a
factory default: the device applies it on first boot and on factory reset only.

**Current state (2026-11-22).** Epics 0–13 and Batch #200 are on `dev` and `main`. The end-to-end
flow works: build, download, browser flashing, channel share, profiles, the build cache, fleet runs,
push config and module settings. The CLI YAML import also fills the module settings (#196). The
manual hardware checks are open in #177.

## Spec

Design lives in `docs/specs/`. Read `docs/specs/spec.md` and the relevant
`docs/specs/features/*.md` before you build a feature. The `## Terms` table in
`docs/specs/spec.md` is the project's controlled vocabulary. Use its words.
`docs/specs/pages/inherit-reconciliation.md` records the upstream research.

Regenerate the rendered spec after an edit: `python3 docs/specs/render-spec.py`. Never
edit `docs/specs/spec.html` or `docs/specs/html/` by hand.

## Stack and layout

- Python 3.13 (uv manages the environment), FastAPI + uvicorn, one process.
- Jinja2 templates + htmx (Epic 3), Bootstrap 5.3. No JavaScript build step.
- SQLite through aiosqlite. PlatformIO for firmware builds.

| Path | Holds |
|---|---|
| `mtfwbuilder/main.py` | App factory, lifespan, middleware |
| `mtfwbuilder/config.py` | Settings (`config.yaml`, then `MTFW_*` env vars) |
| `mtfwbuilder/routers/` | HTTP routes, all API routes under `/api/v1` |
| `mtfwbuilder/services/` | Generator, userPrefs key reader, build service, registry, firmware updater, cleanup |
| `templates/`, `static/` | Pages and assets. Vendored libraries go in `static/vendor/`. |
| `tests/` | pytest suite. Fixtures in `tests/fixtures/`. |
| `docs/adr/` | Architecture decision records |
| `docs/external.md` | Environment variable names and upstream services. Never values. |

## Commands

```bash
uv sync                               # install, with the dev group
uv run python -m playwright install chromium   # browser for the E2E tests in tests/e2e/
uv run pytest                         # tests
uv run ruff check mtfwbuilder/ tests/ # lint
uv run ruff format mtfwbuilder/ tests/ # format
uv run uvicorn mtfwbuilder.main:app --reload --port 5000   # dev server
docker compose up -d           # Docker
```

Docker operations, for the `mtfwbuilder` service in `docker-compose.yml`:

```bash
docker compose logs -f                      # follow the container logs
docker compose exec mtfwbuilder /bin/bash   # open a shell in the running container
docker compose ps                           # show the container status
git pull && docker compose build --no-cache && docker compose up -d   # update and rebuild
```

The project has no real PlatformIO in tests. Tests use a fake `pio` (feature 06).

## Branch model: dev-and-live

- `dev` is the integration branch. Every batch merges to `dev`.
- `main` is the live branch. Promotion from `dev` to `main` is a separate approved step.
- Create a feature branch from `dev`. Open the pull request to `dev`.

## Conventions

- No `print()`. Use `logging`.
- No `shell=True` in a subprocess call.
- No secret in a log line, a database row, a cache entry or browser storage. Secrets are
  PSKs, WiFi passwords, MQTT passwords and admin keys.
- No admin password in a log line, with one exception. At a start with no admin password, the
  project logs the generated password once. A later start never logs it (FR-platform-modernization-17).
- Every route validates its body with a pydantic model.
- Every userPrefs key the generator writes must be a key the firmware source reads. The
  contract test checks this.
- Describe an upstream interface from its documentation or source, never from memory.
- Commit style: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- License: GPL-3.0-or-later (ADR 0004).

## Filing gate

- **A finding earns a tracker issue in five cases, and never otherwise.** Behavior. A
  user-visible output. A guard that guards nothing. A blocked epic. A question the
  maintainer must rule. One carve-out: anything under `docs/specs/` that describes the
  wrong product earns a spec-update issue, because the spec decides what gets built.

  **Every other finding is repaired by the change set that found it, and no issue is
  filed.** A falsified sentence, a moved citation, a stale count, a missing term and prose
  drift each reach the repair. A repair that turns out to touch behavior stops and is
  filed as a behavior finding.

  Where the repair goes:
  - Found while building an issue → that issue's own PR, limited to files it already
    touches.
  - Found at review of an epic integration branch → a documentation commit on that
    branch, before the CI-trigger commit.
  - Found with nothing open → the next batch's integration branch, as its first
    documentation commit.

- A changelog or spec-changelog entry records a **decision**, made at a batch gate. No
  rule, hook, CI check, or test may require a changelog row, a spec edit, or a
  docs-accuracy pass **per change set or per batch member** — a batch that shipped as
  planned earns no line. A test that asserts per-member bookkeeping agreement re-creates
  the churn the filing gate exists to prevent, and fails review here.
