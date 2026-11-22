# External facts

Names and pointers only. Never a value, a secret or a token. This file is committed.

## Environment variables

Settings read `config.yaml` first, then `MTFW_<FIELD>` environment variables
(`mtfwbuilder/config.py`).

| Name | Purpose | Status |
|---|---|---|
| `MTFW_LOG_LEVEL` | Log level | built |
| `MTFW_LOG_JSON` | JSON log format | built |
| `MTFW_DEBUG` | Debug mode | built |
| `MTFW_BASE_DIR` | Project root. Holds `config.json` and the old paths that the startup move reads. | built |
| `MTFW_FIRMWARE_DIR` | Firmware source directory. Default `firmware/` in the data directory. | built |
| `MTFW_TEMP_DIR` | Build directories. Default `artifacts/` in the data directory. | built |
| `MTFW_DATABASE_PATH` | SQLite file. Default `mtfwbuilder.db` in the data directory. | built |
| `MTFW_MAX_QUEUE_SIZE` | Build queue limit | built; used by the build queue |
| `MTFW_BUILD_WORKERS` | Number of build workers | built; each build worker after the first builds in its own copy `<firmware_dir>/.worker-<n>/` of the firmware source |
| `MTFW_BUILD_TIMEOUT_SECONDS` | Build timeout | built |
| `MTFW_CLEANUP_INTERVAL_SECONDS` | Cleanup task interval | built |
| `MTFW_BUILD_MAX_AGE_SECONDS` | Artifact age limit | built |
| `MTFW_ADMIN_PASSWORD_HASH` | Admin bcrypt hash | built |
| `MTFW_SECRET_KEY` | Session and CSRF signing key | built |
| `MTFW_SESSION_MAX_AGE` | Session length | built |
| `MTFW_BUILD_RATE_LIMIT`, `MTFW_LOGIN_RATE_LIMIT` | Rate limits of the build route and the login route, in slowapi notation (`5/minute`) | built |
| `MTFW_DATA_DIR` | Data directory. Holds firmware sources, artifacts, the database and `config.yaml`. Default `data/` in the project root, `/data` in Docker. | built |
| `MTFW_TRUSTED_PROXIES` | Comma-separated addresses or subnets of the proxies whose `X-Forwarded-For` the rate limiter reads, and whose `X-Forwarded-Proto: https` makes the session cookie `Secure`. Default empty: the limiter counts the peer address, and only an `https` request scheme makes the cookie `Secure`. | built |
| `MTFW_INSTALL_TIMEOUT_SECONDS` | Time limit of `pio pkg install` in a firmware install, default 3600 | built |
| `MTFW_CACHE_ENABLED` | Build cache on or off. Default `true`. `false` turns the build cache off. | built |
| `MTFW_CACHE_TTL_SECONDS` | Time after its last use that the cleanup task removes a cache entry. Default 86400. | built; the build cache is `cache/` in the data directory |
| `MTFW_MAX_FLEET_SIZE` | Fleet size limit: the most fleet devices that one fleet holds. Default 50. | built |
| `MTFW_PUSH_TEST_HOOKS` | Test hook flag of the push config page. Default `false`. Only the E2E test config sets `true`. Never set it in production. | built |
| `GITHUB_TOKEN` | Optional. Raises the GitHub API rate limit for firmware lists and source downloads | built |
| `PLATFORMIO_FORCE_COLOR`, `PLATFORMIO_NO_ANSI` | Set by the build for `pio` | built |
| `PLATFORMIO_CORE_DIR` | PlatformIO toolchain directory. The Docker image sets `/home/app/.platformio`, a volume. | built |

## Build cache and secrets

A cache entry holds only artifacts, never the userPrefs file or the build log. An artifact holds the secrets of
its config, so a build whose config holds a secret stores no cache entry and never gets one. A 1-byte channel PSK
selects a published default key, so it is not a secret here. `MTFW_CACHE_TTL_SECONDS` sets how long a cache
entry stays on disk after the last use. With `MTFW_CACHE_ENABLED=false`, a build stores no cache entry.

## Upstream services

| Service | Use | Docs |
|---|---|---|
| GitHub REST API (releases) | Firmware version list and zipball | https://docs.github.com/en/rest/releases/releases |
| api.meshtastic.org | Stable/alpha firmware list (`GET /github/firmware/list`) | No published docs. Shape from `meshtastic/web-flasher` `types/api.ts`. |
| meshtastic/firmware | Firmware source | https://github.com/meshtastic/firmware |
| meshtastic/protobufs | Enum and config reference | https://github.com/meshtastic/protobufs |
| npm registry (`@meshtastic/core`, `@meshtastic/transport-web-serial`, `@meshtastic/transport-web-bluetooth`) | Push config library versions and tarball SHA-1 values | https://registry.npmjs.org/@meshtastic%2Fcore. ADR 0008. |
| esm.sh | Source of the vendored push config ES modules. A maintainer downloads them once; the pages never load from it. | https://github.com/esm-dev/esm.sh. ADR 0008 holds the URLs and SHA-256 values. |

## Support

Operators file problems at https://github.com/Crank-Git/MTFWBuilder/issues.
