# Multi-stage build for smaller production image
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    gcc \
    g++ \
    make \
    pkg-config \
    libusb-1.0-0-dev \
    udev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app
USER app

# Set up Python virtual environment
ENV VIRTUAL_ENV=/app/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy everything and install (hatchling needs the package directory present)
COPY --chown=app:app . .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Create necessary directories
RUN mkdir -p /app/firmware /app/temp /app/logs

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=30s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Run with uvicorn (production ASGI server)
CMD ["uvicorn", "mtfwbuilder.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
