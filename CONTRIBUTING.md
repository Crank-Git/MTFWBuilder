# Contributing to MTFWBuilder

Thanks for your interest in contributing! This project serves the Meshtastic community
and welcomes contributions of all kinds.

## Getting Started

```bash
# Clone and set up
git clone https://github.com/Crank-Git/MTFWBuilder.git
cd MTFWBuilder
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Run the tests
pytest

# Start the dev server
uvicorn mtfwbuilder.main:app --reload --port 5000
```

## Project Structure

```
mtfwbuilder/           # Python package (FastAPI backend)
  main.py              # App factory and lifespan
  config.py            # Settings (pydantic-settings)
  auth.py              # Session auth (bcrypt + signed cookies)
  models.py            # Pydantic request/response models
  database.py          # SQLite schema and queries
  routers/             # API route handlers
  services/            # Business logic (build, config gen, device registry)
devices/variants.yaml  # Device registry (single source of truth)
templates/             # Jinja2 templates with htmx
static/                # CSS, JS
tests/                 # pytest suite
```

## Adding a New Device Variant

1. Edit `devices/variants.yaml`
2. Add an entry with:
   - `id`: Must match a PlatformIO environment name exactly
   - `name`: Human-readable display name
   - `manufacturer`: Brand/manufacturer
   - `architecture`: `esp32`, `nrf52`, or `rp2040`
3. Run `pytest tests/test_device_registry.py` to validate
4. Submit a PR

## Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=mtfwbuilder

# Specific test file
pytest tests/test_config_generator.py -v
```

## Code Style

- Python 3.10+, type hints encouraged
- Format with `black` (line length 120)
- Lint with `ruff`
- No `print()` — use `logging` module
- No `shell=True` in subprocess calls

## Pull Requests

1. Create a feature branch from `main`
2. Make your changes
3. Ensure all tests pass (`pytest`)
4. Submit a PR with a clear description

## Reporting Issues

Use [GitHub Issues](https://github.com/Crank-Git/MTFWBuilder/issues). Include:
- What you expected vs. what happened
- Device variant (if applicable)
- Build logs (if a build failed)
