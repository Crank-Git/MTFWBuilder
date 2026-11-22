"""MTFWBuilder - Meshtastic firmware configuration generator and builder."""

from importlib.metadata import version

# uv sync and the Docker image install the project, so the version in `pyproject.toml` is in its metadata.
__version__ = version("mtfwbuilder")
