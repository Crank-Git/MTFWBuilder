# 0001 — FastAPI backend in one process

Date: 2026-11-21
Status: accepted

Reconstructed by `/project-inherit` from commits `b88d69b`–`c9c83d2` (March 2026). The
choice was made then; this record was written later.

## Context

v1 was a 1,117-line Flask app (`app.py`) that ran each build in the request. A build
takes minutes, so the request blocked, and the page had no progress.

## Decision

The project uses FastAPI with uvicorn, one process (`--workers 1`). Builds run as async
tasks. The progress stream uses SSE (`sse-starlette`).

## Consequences

- The build queue lives in memory, with no broker. More than one uvicorn worker would
  split it, so the project stays at one process.
- FastAPI publishes OpenAPI docs at `/docs`.
- `asyncio.timeout` needs Python 3.11 or later.
