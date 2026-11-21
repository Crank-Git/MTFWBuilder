---
name: run
description: Start MTFWBuilder locally for a manual check. Use when asked to run, launch, or screenshot the app, or to see a change in the real pages.
allowed-tools: Bash, Read
---

# Run MTFWBuilder

1. Install the project: `uv sync`.
2. Start the dev server: `uv run uvicorn mtfwbuilder.main:app --reload --port 5000`.
3. Open `http://localhost:5000/`. The page loads when the server is up.
4. For a real build, an admin must install a firmware source:
   1. Open `http://localhost:5000/admin`.
   2. Sign in. The default password is `meshtastic` when `config.yaml` sets none.
   3. Install the stable firmware version. The first install downloads toolchains
      (several GB) and takes many minutes.
5. For a fast check without PlatformIO, use the fake `pio` from the test fixtures
   (feature 06) on `PATH`.

Docker: `docker compose up -d`, then `docker compose logs -f`.

Warning: Web Serial flashing (feature 13) needs `localhost` or HTTPS. A LAN address over
plain HTTP does not work.
