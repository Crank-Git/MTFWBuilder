# 0006 — Generate the registry from `pio project config`

Date: 2026-11-21
Status: accepted

## Context

The registry was a hand-kept YAML file, and 71 of its 150 ids were not PlatformIO environment names. Upstream variant `platformio.ini` files carry `custom_meshtastic_*` metadata and use `extends`, `extra_configs` and `${...}` interpolation. Two ways to read them were considered: our own `configparser` reader, or PlatformIO's own resolved output.

## Decision

The project runs `pio project config --json-output` in each firmware source when it becomes ready, and stores one row per non-native env in the `variants` table for that firmware version. Tests use a recorded output (`tests/fixtures/firmware/project_config.json`) through the fake `pio`.

## Consequences

- PlatformIO resolves `extends` and interpolation, so the registry matches what a build sees.
- Registry generation needs PlatformIO on the server, which builds need anyway.
- A bad value in one env skips that field or env with a warning; the rest of the registry is stored.
- The weekly upstream check does not yet regenerate the registry against upstream (#144).
