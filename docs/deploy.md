# Deploy

This page covers Docker: the development stack, the production overlay with nginx and HTTPS,
the upgrade from older releases, the volumes and troubleshooting.

## Development

```bash
docker compose up -d
docker compose logs -f
```

The container listens on port 5000. `docker-compose.yml` publishes port 5000 on every host
interface.

## Production

The production overlay puts nginx in front of the project and serves HTTPS. Web Serial and
Web Bluetooth work only on a secure page. Thus browser flashing and push config need HTTPS
on every host other than `localhost`.

1. Copy the nginx config: `cp nginx/nginx.conf.example nginx/nginx.conf`.
2. In `nginx/nginx.conf`, set `server_name` to your domain.
3. Put the certificate in `nginx/ssl/fullchain.pem` and the key in `nginx/ssl/privkey.pem`.
4. Start the project with the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The overlay removes the published port 5000. Only nginx publishes ports: 80 and 443.

The project has no default admin password. On first start, the project generates a random
password and logs it once. Read the generated password from the first-start log:

```bash
docker compose logs mtfwbuilder | grep "Generated admin password"
```

The project keeps only the hash of the password in `/data/config.yaml`. A later start does not
log the password again.

To set your own admin password, write your `config.yaml` into the `data` volume, then restart.
The command runs as the container user, so the project can rewrite the file when it hashes a
plaintext password.

```bash
docker compose exec -T mtfwbuilder tee /data/config.yaml < config.yaml > /dev/null
docker compose restart mtfwbuilder
```

The overlay sets `MTFW_TRUSTED_PROXIES` to `172.30.80.0/24`, the fixed subnet of its `backend`
network. Only nginx and the app use that network. The app then reads two headers from nginx:
- The rate limiter reads `X-Forwarded-For`.
- The session cookie reads `X-Forwarded-Proto`. When the value is `https`, the cookie is `Secure`.

The app ignores both headers from an address outside `MTFW_TRUSTED_PROXIES`. If you change the
subnet in `docker-compose.prod.yml`, change both values.

The project signs sessions and CSRF tokens with `secret_key`, and it derives the profile secret
key from it (ADR 0007). The project gets `secret_key` from the first source in this list:
1. `secret_key` in `/data/config.yaml`.
2. The `MTFW_SECRET_KEY` environment variable.
3. A new random key. The project writes the new key to `/data/config.yaml`.

On first start without `MTFW_SECRET_KEY`, the project writes a key to `config.yaml`. After that
start, `MTFW_SECRET_KEY` has no effect until you remove `secret_key` from `config.yaml`. A
change of `secret_key` ends every session, and the stored profile secrets do not decrypt.

## Upgrade from a release before the `data` volume

Older releases used the named volumes `firmware_data`, `build_cache`, `temp_data`, `logs` and
`platformio_cache`. The compose files do not mount them now, so the new container does not
see the old firmware source.

1. Start the new release.
2. Go to `/admin`, sign in, and click Install stable to install a firmware source in the
   `data` volume.
3. When the first build succeeds, remove the old volumes with `docker volume rm`. Compose
   adds the project name to each volume name, for example `mtfwbuilder_firmware_data`. The
   `build_cache` volume can hold object files that contain secrets.

## Upgrade of a manual install

At startup, the project moves a `config.yaml` in the project root to `data/config.yaml`. It
moves the old `mtfwbuilder.db` and `firmware/` only when `MTFW_DATABASE_PATH` and
`MTFW_FIRMWARE_DIR` have their default values. An old `firmware/` that stays in the project
root is scrubbed and kept, and a warning names it. In Docker, `config.yaml` is
`/data/config.yaml` in the `data` volume. The image does not copy a `config.yaml` from the
project root.

If no `config.yaml` exists, the app migrates from `config.json` (v1 format) or uses the
defaults. At startup, the app hashes a plaintext password with bcrypt. If no admin password is
set, the app generates one and logs it once.

## Volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `data` | `/data` | The data directory: firmware sources, artifacts, the database and `config.yaml` |
| `platformio` | `/home/app/.platformio` | PlatformIO packages and toolchains, so a new container does not download them again |

## Troubleshooting

**Build fails with "Firmware source not installed"**
- Go to `/admin`, sign in, and click Install stable to download a firmware source from GitHub.

**Configuration does not take effect (ESP32)**
- Use `firmware.factory.bin`. It holds the bootloader and the partition table.
- Erase the flash first: `esptool.py --chip esp32 erase_flash`.

**Install or build times out**
- An install runs `pio pkg install` for the firmware source, which downloads the platforms and
  toolchains. The limit is `install_timeout_seconds` (default 3600).
- A build has the limit `build_timeout_seconds` (default 900).
- Docker: make sure that the `platformio` volume is mounted.

**Docker disk space issues**
- A periodic cleanup runs at startup and then each `cleanup_interval_seconds` (default 30
  minutes). It deletes the build directories older than `build_max_age_seconds` (default 1
  hour).
- With an admin session, `POST /api/v1/cleanup` runs the same cleanup at once.
