---
name: refresh-fixtures
description: Update tests/fixtures/firmware/ from upstream Meshtastic firmware. Use when a new firmware version ships, or when the weekly upstream check fails.
allowed-tools: Bash, Read, Edit
---

# Refresh the firmware fixtures

1. Shallow-clone the upstream tag into a scratch directory outside the repository:
   `git clone --depth 1 --branch <tag> https://github.com/meshtastic/firmware <scratch>/fw`.
2. Copy these files into `tests/fixtures/firmware/`, keeping their relative paths:
   - `platformio.ini`
   - `userPrefs.jsonc`
   - `bin/platformio-custom.py`
   - the variant `platformio.ini` files the fixture already holds
3. Record the tag and the date in `tests/fixtures/firmware/SOURCE.md`.
4. Run `pytest`.
5. If a contract test fails, read the upstream change before you change the generator.
   State the upstream file and line in the PR body.

Do not run `pio run` for a fixture refresh. Do not copy `src/` beyond what a test needs.
