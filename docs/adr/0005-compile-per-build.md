# 0005 — Compile firmware per build with PlatformIO

Date: 2026-11-21
Status: accepted

Reconstructed by `/project-inherit` from the v1 and v2 build code (June 2025, March
2026).

## Context

A userPrefs value reaches the firmware only as a `-D` compile flag. Upstream has no
mechanism to inject a config into a prebuilt binary.

## Decision

The project compiles each build with `pio run -e <variant>` in a firmware source on the
server.

## Consequences

- A build takes minutes and gigabytes of toolchains. The build cache (feature 14)
  reduces repeats.
- Secrets pass through the build flags and the build log. The project masks the log and
  scrubs the firmware source (feature 06).
- Push config (feature 15) is the path for devices that already run stock firmware.
