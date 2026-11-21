# MTFWBuilder

A self-hosted web application for generating Meshtastic device configurations and building custom firmware. Configure your device, bake settings into firmware, and download ready-to-flash binaries — all from one interface.

![MTFWBuilder Interface](image/mtfw.png)

## Status

The v2 API under `/api/v1` exists. The pages still call v1 URLs, so the flow from the pages does not work end to end. Two planned features repair it:

- `build-pipeline-repair` (`docs/specs/features/06-build-pipeline-repair.md`)
- `frontend-htmx` (`docs/specs/features/07-frontend-htmx.md`)

In this README, "planned" marks a part that the code does not do yet. The spec in `docs/specs/` describes the planned project.

## What It Does

**MTFWBuilder is the only tool in the Meshtastic ecosystem that combines configuration generation with firmware compilation in a self-hosted package.**

- The official [Web Flasher](https://flasher.meshtastic.org/) flashes pre-built firmware
- The [Python CLI](https://github.com/meshtastic/Meshtastic-python) configures devices after flashing
- **MTFWBuilder bakes your config INTO the firmware before flashing**

### Features

- **Configuration Generator** — Web UI for building `userPrefs.jsonc` files with channel setup, LoRa settings, GPS, WiFi/MQTT, and more
- **Firmware Builder** — Compiles custom firmware via PlatformIO with your config pre-loaded
- **Device registry** — `devices/variants.yaml` lists the variants. The count of entries in that file is the variant count, and the startup log states it. ESP32 variants give `.bin` files. nRF52 and RP2040 variants give `.uf2` files.
- **Factory Binary Support** — ESP32 devices get both `firmware.bin` and `firmware.factory.bin`
- **Build progress (planned)** — The progress stream `GET /api/v1/build-progress/{id}` exists. The build page does not open it yet.
- **Admin Dashboard** — Sign-in, firmware sources (install and remove firmware versions), build cleanup and recent builds.
- **Dark Mode** — Default dark theme with light mode toggle

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Crank-Git/MTFWBuilder.git
cd MTFWBuilder
docker compose up -d
```

Open `http://localhost:5000`

### Manual

```bash
git clone https://github.com/Crank-Git/MTFWBuilder.git
cd MTFWBuilder
# uv installs Python 3.13 and the dependencies: https://docs.astral.sh/uv/getting-started/installation/
uv sync --no-dev
uv run uvicorn mtfwbuilder.main:app --host 0.0.0.0 --port 5000
```

### Configuration

The data directory holds all state: firmware sources, artifacts, the database and `config.yaml`. `MTFW_DATA_DIR` sets it. The default is `data/` in the project root, and `/data` in Docker.

```bash
cp config.yaml.example data/config.yaml
# Edit data/config.yaml — set admin_password and secret_key
```

At startup, the project moves a `config.yaml` in the project root to `data/config.yaml`. It moves the old `mtfwbuilder.db` and `firmware/` only when `MTFW_DATABASE_PATH` and `MTFW_FIRMWARE_DIR` have their default values. An old `firmware/` that stays in the project root is scrubbed and kept, and a warning names it. In Docker, `config.yaml` is `/data/config.yaml` in the `data` volume. The image does not copy a `config.yaml` from the project root.

If no `config.yaml` exists, the app auto-migrates from `config.json` (v1 format) or uses defaults. Plaintext passwords are auto-hashed to bcrypt on first startup.

`docs/external.md` lists every `MTFW_*` environment variable.

## Usage

The steps below are the flow that the planned repair gives (see [Status](#status)).

### 1. Generate Configuration

1. Fill in device name, owner info, timezone
2. Configure channels with encryption (PSK auto-generated)
3. Set LoRa region and modem preset
4. Configure GPS, WiFi, MQTT as needed
5. Preview and download `userPrefs.jsonc`

### 2. Build Firmware

1. Upload your `userPrefs.jsonc` or use the config you just generated
2. Select your device variant from the dropdown
3. Click **Build Firmware**
4. Download your custom firmware file

### 3. Flash Your Device

**ESP32 (.bin)** — Use the factory binary for best results:
```bash
esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 921600 write_flash 0x0 firmware.factory.bin
```

**nRF52/RP2040 (.uf2)** — Put device in DFU mode, drag and drop the `.uf2` file.

### 4. Admin Panel

The `/admin` page shows the sign-in form. Sign in with the admin password from `config.yaml`. The Firmware sources section then lists the installed firmware versions and the upstream firmware versions. It installs stable, alpha or an exact tag, shows the install progress, and removes a firmware version. When no firmware version is installed, Install stable is the first action.

## Architecture

```
MTFWBuilder/
├── mtfwbuilder/                    # FastAPI application
│   ├── main.py                     # App factory, lifespan, middleware
│   ├── config.py                   # Settings (config.yaml, then MTFW_* env vars)
│   ├── auth.py                     # Bcrypt + signed cookie sessions
│   ├── database.py                 # SQLite (build history, config profiles, firmware sources)
│   ├── models.py                   # Pydantic request/response validation
│   ├── rate_limit.py               # slowapi rate limiting
│   ├── routers/
│   │   ├── config_generator.py     # /api/v1/generate, preview, download
│   │   ├── firmware_builder.py     # /api/v1/build-firmware, SSE progress
│   │   ├── firmware.py             # /api/v1/firmware, installed and upstream firmware versions
│   │   ├── variants.py             # /api/v1/variants, registry of a firmware version, device images
│   │   ├── admin.py                # Login, firmware updates, cleanup
│   │   └── pages.py                # HTML page routes
│   └── services/
│       ├── jsonc_generator.py      # userPrefs.jsonc generation
│       ├── build_service.py        # Async PlatformIO build pipeline
│       ├── device_registry.py      # YAML device variant registry
│       ├── firmware_updater.py     # GitHub firmware source downloads
│       ├── firmware_catalog.py     # Upstream stable and alpha firmware versions
│       └── cleanup_service.py      # Build artifact and PSK cleanup
├── devices/variants.yaml           # Registry (the variant list)
├── templates/                      # Jinja2 templates (htmx planned)
├── static/                         # CSS, JS
├── tests/                          # pytest suite
├── data/                           # Data directory (MTFW_DATA_DIR). Not in git.
└── pyproject.toml                  # Modern Python packaging
```

### Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Backend | FastAPI + uvicorn | Async builds, WebSocket-ready, auto OpenAPI docs |
| Frontend | Jinja2 + Bootstrap 5 (htmx planned) | Dynamic UI without a JS build pipeline |
| Build Progress | Server-Sent Events (SSE) | One-way progress, proxy-friendly. The build page does not open it yet. |
| Database | SQLite (aiosqlite) | Zero-config. Build history and profiles are planned. |
| Auth | bcrypt + itsdangerous | Hashed passwords, signed cookie sessions |
| Device Registry | YAML | Human-readable, git-diffable, easy to contribute |
| Build System | PlatformIO | Industry standard for embedded firmware |
| Python | 3.13 | `.python-version` and `requires-python` set it |
| Packaging | pyproject.toml + hatchling + uv | `uv.lock` pins every dependency |

### API

Auto-generated OpenAPI documentation at `/docs` when the app is running.

Key endpoints:
- `POST /api/v1/generate` — Generate config from JSON
- `POST /api/v1/preview` — Preview config without downloading
- `POST /api/v1/download` — Download `userPrefs.jsonc`
- `POST /api/v1/build-firmware` — Start firmware build
- `GET /api/v1/build-progress/{id}` — SSE build progress stream
- `GET /api/v1/builds/{id}` — Build status of a build: `build_id`, `status`, `message`, `progress`, `error`, `download_url`
- `POST /api/v1/builds/{id}/cancel` — Cancel a queued or running build
- `GET /api/v1/builds/{id}/artifacts` — Artifact list of a build: `name`, `size`, `kind`
- `GET /api/v1/builds/{id}/artifacts/{name}` — Download one artifact from the artifact list
- `GET /api/v1/download-firmware/{id}` — Redirects (308) to the factory or primary artifact, for one release
- `GET /api/v1/system-info` — The default firmware version (`version`, `last_updated`) and the list
  `firmware_versions`, read from the `firmware_sources` table. Without a firmware source, `version`
  is `Not installed`.
- `GET /api/v1/firmware` — Installed firmware versions, the newest first: `version`, `channel`
  (`stable`, `alpha` or `pinned`), `installed_at`, `size_bytes`, `status`
- `GET /api/v1/firmware/available` (admin) — Upstream firmware versions, the newest first:
  `source` (`api.meshtastic.org` or `github`), and the lists `stable` and `alpha`. Each
  entry has `tag`, `title`, `date` and `revoked`. Returns 502 when both upstreams fail.
- `POST /api/v1/firmware/install` (admin) — Install by `{"channel": "stable"}`, `{"channel": "alpha"}`
  or `{"tag": "<tag>"}`. Returns 202 with `install_id`, `progress_url` and `warning`, and 409 when
  the firmware version is installed. `warning` is not empty when the upstream firmware list marks
  the exact tag as revoked. One install runs at a time, and the next install waits in `queued`.
- `GET /api/v1/firmware/install/{install_id}` (admin) — Install status of an install: `install_id`,
  `version`, `status`, `error`. Returns 404 for an unknown install.
- `GET /api/v1/firmware/install/{install_id}/progress` (admin) — SSE install progress stream
- `DELETE /api/v1/firmware/{version}` (admin) — Remove an installed firmware version. Returns 204,
  and 409 while a build uses it.
- `GET /api/v1/variants?version=&q=&arch=&experimental=` — Registry entries of a firmware version, in env
  order. Without `version`, the registry of the default firmware version. `q` matches the display name, the
  env or the manufacturer without case and punctuation. Give `arch` once for each architecture to keep.
  Variants with support level `extra` show only with `experimental=true`. Each entry has the registry
  fields and `image_url`, which is null when the firmware source does not hold the image file. Returns 404
  for a `version` that is not installed.
- `GET /api/v1/variants/{env}/image?version=` — The SVG or PNG device image from the firmware source.
  Returns 404 when the firmware source does not hold the image file named in `custom_meshtastic_images`.

Each admin POST and DELETE requires the CSRF token. A form sends it in the field `csrf_token`.
A JSON request and a DELETE request send it in the header `X-CSRF-Token`. Without a valid token,
the route returns 403.

The three config routes validate the body with `ConfigRequest`. An invalid body returns
422 with field errors, for example `channel_0.psk`.

`/api/v1/preview` and `/api/v1/download` also take a form body with the config fields,
for example `channel_0[psk]`. When the request has the header `HX-Request: true`, the
preview returns an HTML fragment, and a 422 returns a fragment with the field errors.
Every other request gets JSON.

`/api/v1/build-firmware` takes the config by the form field `config_source`:

- `form`: the config fields, as the config form posts them.
- `current`: one field `config_json` that holds the config as JSON.
- `upload` (the default): one field `userPrefs` that holds a userPrefs file.

The optional form field `firmware_version` names an installed firmware version, for example
`v2.8.2.d5f1619`. Without it, the build uses the newest installed stable firmware version.
A firmware version that is not installed gives 422 with a field error for `firmware_version`.
A `variant` that is not in the registry of the firmware version gives 422 with a field error for
`variant`, for example "Variant not in v2.8.2.d5f1619". A firmware version without a registry uses
`devices/variants.yaml`.

An htmx request to `/api/v1/build-firmware` or `/api/v1/builds/{id}` gets the progress
panel as an HTML fragment. The page `/?build=<id>` shows the progress panel of that build.

## Docker

### Development

```bash
docker compose up -d
docker compose logs -f
```

### Production

The production overlay puts nginx in front of the project and serves HTTPS. You need HTTPS for two reasons:

- The admin session cookie has the `Secure` flag unless `debug` is on. A browser does not send it over plain HTTP to a remote host.
- Web Serial works only on a secure page, so browser flashing (planned) needs HTTPS.

1. Copy the nginx config: `cp nginx/nginx.conf.example nginx/nginx.conf`.
2. In `nginx/nginx.conf`, set `server_name` to your domain.
3. Put the certificate in `nginx/ssl/fullchain.pem` and the key in `nginx/ssl/privkey.pem`.
4. Start the project with the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

To set the admin password, write your `config.yaml` into the `data` volume, then restart. The command runs as the container user, so the project can rewrite the file when it hashes a plaintext password.

```bash
docker compose exec -T mtfwbuilder tee /data/config.yaml < config.yaml > /dev/null
docker compose restart mtfwbuilder
```

### Upgrade from a release before the `data` volume

Older releases used the named volumes `firmware_data`, `build_cache`, `temp_data`, `logs` and `platformio_cache`. The compose files do not mount them now, so the new container does not see the old firmware source.

1. Start the new release.
2. Go to `/admin`, sign in, and click Install stable to install a firmware source in the `data` volume.
3. When the first build succeeds, remove the old volumes with `docker volume rm`. Compose adds the project name to each volume name, for example `mtfwbuilder_firmware_data`. The `build_cache` volume can hold object files that contain secrets.

### Volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `data` | `/data` | The data directory: firmware sources, artifacts, the database and `config.yaml` |
| `platformio` | `/home/app/.platformio` | PlatformIO toolchains (~200MB per architecture), so a new container does not download them again |

## Security

- Admin passwords stored as **bcrypt hashes** (auto-migrated from plaintext)
- Session cookies: **httpOnly, SameSite=Lax, Secure** (unless `debug` is on)
- When a build ends, the project **scrubs** the firmware source: it deletes `userPrefs.jsonc`, `.pio/build_cache` and the `.pio/build/<env>/` directory of the build. A failed build and a cancelled build also scrub.
- Build directories **isolated** per build. A download does not delete a build directory. The periodic cleanup deletes it when it is older than `build_max_age_seconds`.
- All subprocess calls use **parameterized arguments** (no `shell=True`)
- **Rate limiting** on the login and build endpoints. The defaults are `10/minute` (`MTFW_LOGIN_RATE_LIMIT`) and `5/minute` (`MTFW_BUILD_RATE_LIMIT`).
- Behind a proxy, set `MTFW_TRUSTED_PROXIES` to the proxy addresses or subnets, comma-separated. The rate limiter then reads `X-Forwarded-For` from those proxies. The default is empty: the limiter counts the peer address.
- The production overlay sets `MTFW_TRUSTED_PROXIES` to `172.30.80.0/24`, the fixed subnet of its `backend` network. Only nginx and the app use that network. If you change the subnet in `docker-compose.prod.yml`, change both values.
- File uploads **checked** for size (64KB max) and UTF-8 encoding only
- **Path traversal protection** on firmware download endpoint

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=mtfwbuilder

# Format
uv run ruff format mtfwbuilder/ tests/

# Lint
uv run ruff check mtfwbuilder/ tests/

# Dev server with hot reload
uv run uvicorn mtfwbuilder.main:app --reload --port 5000
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines.

## Troubleshooting

**Build fails with "Firmware source not installed"**
- Go to `/admin`, sign in, and click Install stable to download a firmware source from GitHub

**Configuration not taking effect (ESP32)**
- Always use `firmware.factory.bin` — it includes bootloader and partition table
- Try a full erase first: `esptool.py --chip esp32 erase_flash`

**Build times out after 15 minutes**
- First build for each architecture downloads the toolchain (~200MB)
- Subsequent builds use cached toolchains and are much faster
- Docker: ensure the `platformio` volume is mounted

**Docker disk space issues**
- A periodic cleanup runs at startup and then each `cleanup_interval_seconds` (default 30 minutes). It deletes the build directories older than `build_max_age_seconds` (default 1 hour).
- With an admin session, `POST /api/v1/cleanup` runs the same cleanup at once.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) in the repository.

## Acknowledgments

- [Meshtastic](https://meshtastic.org/) — The mesh networking platform
- [PlatformIO](https://platformio.org/) — Firmware build system
- [FastAPI](https://fastapi.tiangolo.com/) — Modern Python web framework
- [htmx](https://htmx.org/) — Dynamic HTML without JavaScript frameworks
- [Bootstrap](https://getbootstrap.com/) — UI components

## Related Projects

- [Meshtastic Firmware](https://github.com/meshtastic/firmware) — Official firmware
- [Meshtastic Web Flasher](https://flasher.meshtastic.org/) — Official web flasher
- [Meshtastic Python](https://github.com/meshtastic/Meshtastic-python) — Python API
