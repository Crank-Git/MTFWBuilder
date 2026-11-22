# Contributing to MTFWBuilder

Thanks for your interest in contributing! This project serves the Meshtastic community
and welcomes contributions of all kinds.

## Getting Started

```bash
# Clone and set up
git clone https://github.com/Crank-Git/MTFWBuilder.git
cd MTFWBuilder
# uv installs Python 3.13 and the dependencies: https://docs.astral.sh/uv/getting-started/installation/
uv sync

# Install the browser for the E2E tests in tests/e2e/
uv run python -m playwright install chromium

# Run the tests
uv run pytest

# Start the dev server
uv run uvicorn mtfwbuilder.main:app --reload --port 5000
```

## Project Structure

```
MTFWBuilder/
├── mtfwbuilder/                    # FastAPI application
│   ├── main.py                     # App factory, lifespan, middleware
│   ├── config.py                   # Settings (config.yaml, then MTFW_* env vars)
│   ├── auth.py                     # Bcrypt, signed cookie sessions, CSRF tokens
│   ├── database.py                 # SQLite: builds, firmware sources, registry, build cache, profiles, fleets
│   ├── models.py                   # Pydantic request/response validation
│   ├── rate_limit.py               # slowapi rate limiting, trusted proxies
│   ├── htmx.py                     # HTML fragment for an htmx request, JSON for other requests
│   ├── meshtastic_enums.py         # Region, modem preset and role values from the protobufs
│   ├── key_bytes.py                # PSK and admin key decoding to the userPrefs byte array
│   ├── time_zones.py               # IANA time zone to POSIX TZ string
│   ├── data/                       # tz_posix.json, the POSIX TZ string table
│   ├── routers/
│   │   ├── config_generator.py     # /api/v1/generate, preview, download
│   │   ├── firmware_builder.py     # /api/v1/build-firmware, SSE progress
│   │   ├── firmware.py             # /api/v1/firmware, installed and upstream firmware versions
│   │   ├── variants.py             # /api/v1/variants, registry of a firmware version, device images
│   │   ├── profiles.py             # /api/v1/profiles, saved configs, with encrypted secrets on opt-in
│   │   ├── fleets.py               # /api/v1/fleets and /api/v1/fleet-runs, fleets and fleet runs
│   │   ├── admin.py                # Login, firmware updates, cleanup
│   │   ├── channel_share.py        # /api/v1/import/channel-url, import/cli-yaml, export/channel-url
│   │   └── pages.py                # HTML page routes
│   └── services/
│       ├── jsonc_generator.py      # userPrefs.jsonc generation
│       ├── userprefs_keys.py       # The userPrefs keys that a firmware source reads
│       ├── secret_mask.py          # Secret mask for the build log
│       ├── build_cache.py          # Build cache, keyed by the config hash
│       ├── build_service.py        # Async PlatformIO build pipeline
│       ├── registry_generator.py   # Registry of a firmware source, from its PlatformIO config
│       ├── firmware_updater.py     # GitHub firmware source downloads
│       ├── firmware_catalog.py     # Upstream stable and alpha firmware versions
│       ├── channel_share.py        # Channel URL decoder and encoder, CLI export YAML reader
│       ├── profiles.py             # Profile records, secret removal on save
│       ├── fleets.py               # Fleet records, fleet device rows, CSV import
│       ├── fleet_runs.py           # One build per fleet device, Retry failed, the zip of a fleet run
│       └── cleanup_service.py      # Build artifact and PSK cleanup
├── templates/                      # Jinja2 pages and htmx partials
├── static/                         # CSS, JS, vendored libraries in static/vendor/
├── tests/                          # pytest suite
├── data/                           # Data directory (MTFW_DATA_DIR). Not in git.
└── pyproject.toml                  # Modern Python packaging
```

## Adding a New Device Variant

The variants come from the firmware source, so this project holds no variant list. When an
admin installs a firmware version, the project reads each env and its `custom_meshtastic_*`
metadata from `pio project config --json-output` (feature 10).

To add a variant, add it to upstream [meshtastic/firmware](https://github.com/meshtastic/firmware).
The next firmware version that holds it shows it in the device picker.

## Running Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=mtfwbuilder

# Specific test file
uv run pytest tests/test_config_generator.py -v
```

The contract tests read the firmware fixture in `tests/fixtures/firmware/`. The weekly
workflow `.github/workflows/upstream-check.yml` runs them against upstream `develop`. To
run the same check locally:

1. Clone upstream outside the repository:
   `git clone --depth 1 --branch develop https://github.com/meshtastic/firmware.git <scratch>/firmware`.
2. Replace the fixture with the clone: `uv run python -m tests.upstream_fixture <scratch>/firmware`.
3. Run the contract tests: `uv run pytest -k contract`.
4. Restore the fixture: `git checkout -- tests/fixtures/firmware/ && git clean -fd tests/fixtures/firmware/`.

If the check fails, refresh the fixture with `.claude/skills/refresh-fixtures/SKILL.md`.

## Code Style

- Python 3.13+, type hints encouraged
- Format with `uv run ruff format` (line length 120)
- Lint with `uv run ruff check`. The rule list is in `pyproject.toml`.
- No `print()` — use `logging` module
- No `shell=True` in subprocess calls

## Pull Requests

1. Create a feature branch from `dev`
2. Make your changes
3. Ensure all checks pass: `uv run pytest`, `uv run ruff check mtfwbuilder/ tests/` and `uv run ruff format --check mtfwbuilder/ tests/`
4. Submit a PR to `dev` with a clear description

`dev` is the integration branch. `main` is the live branch, and a separate approved step
promotes `dev` to `main`.

## Reporting Issues

Use [GitHub Issues](https://github.com/Crank-Git/MTFWBuilder/issues). Include:
- What you expected vs. what happened
- Device variant (if applicable)
- Build logs (if a build failed)
