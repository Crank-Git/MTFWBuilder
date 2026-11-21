# The builder stage resolves the locked environment, so the runtime stage needs no uv and no build tools.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

# The runtime stage has the same interpreter, so the virtual environment uses the system Python.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# A change to the source keeps this dependency layer in the cache.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    MTFW_DATA_DIR=/data \
    PLATFORMIO_CORE_DIR=/home/app/.platformio

# PlatformIO downloads each toolchain itself. It needs git for library dependencies that come from a git URL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Docker copies the owner of an image directory into a new named volume, so the volumes start writable.
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /data /home/app/.platformio \
    && chown app:app /data /home/app/.platformio

# The project is an editable install, so the source stays at the same path as in the builder stage.
COPY --from=builder /app /app

USER app
WORKDIR /app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=5)"]

CMD ["uvicorn", "mtfwbuilder.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
