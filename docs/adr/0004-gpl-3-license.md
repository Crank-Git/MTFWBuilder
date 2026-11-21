# 0004 — Relicense to GPL-3.0

Date: 2026-11-21
Status: accepted

## Context

`pyproject.toml` declared MIT, but the repository had no LICENSE file. Channel URL
import and push config need the Meshtastic protobufs, the `meshtastic` python package,
or the Meshtastic JavaScript libraries. Every one of them is GPL-3.0. The owner is the
sole author. The owner chose in the `/project-inherit` interview.

## Decision

The project is licensed GPL-3.0-or-later. Epic 0 adds the LICENSE file and updates
`pyproject.toml`.

## Consequences

- The project can use every Meshtastic library and copy upstream fixtures.
- Apache-2.0 libraries (esptool-js) stay compatible.
- A contributor's code is GPL-3.0 from the change.
