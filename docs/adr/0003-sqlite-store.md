# 0003 — SQLite as the data store

Date: 2026-11-21
Status: accepted

Reconstructed by `/project-inherit` from `mtfwbuilder/database.py` (commit `b88d69b`).
The schema was created in March 2026 and was not used until this spec.

## Context

A self-hoster runs one container. A database server adds setup and a second container.

## Decision

The project uses SQLite through aiosqlite, in one file under the data directory.

## Consequences

- No setup for the self-hoster.
- One writer at a time, which fits one process.
- The project stores no secret in the database unless an operator opts in per profile,
  and then only encrypted (feature 14).
