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

# Run the tests
uv run pytest

# Start the dev server
uv run uvicorn mtfwbuilder.main:app --reload --port 5000
```

## Project Structure

```
mtfwbuilder/           # Python package (FastAPI backend)
  main.py              # App factory and lifespan
  config.py            # Settings (pydantic-settings)
  auth.py              # Session auth (bcrypt + signed cookies)
  models.py            # Pydantic request/response models
  database.py          # SQLite schema. No route reads or writes it yet.
  routers/             # API route handlers
  services/            # Business logic (build, config gen, device registry)
devices/variants.yaml  # Device registry (single source of truth)
templates/             # Jinja2 templates (htmx planned)
static/                # CSS, JS
tests/                 # pytest suite
data/                  # Data directory (MTFW_DATA_DIR). Not in git.
```

## Adding a New Device Variant

1. Edit `devices/variants.yaml`
2. Add an entry with:
   - `id`: Must match a PlatformIO environment name exactly. Many existing ids do not match yet.
     FR-build-pipeline-repair-20 corrects them.
   - `name`: Human-readable display name
   - `manufacturer`: Brand/manufacturer
   - `architecture`: `esp32`, `nrf52`, `rp2040`, or `stm32`
3. Run `uv run pytest tests/test_device_registry.py` to validate
4. Submit a PR

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
3. Run the contract tests: `uv run pytest -k "contract or registry"`.
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
