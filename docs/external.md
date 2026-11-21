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
| `MTFW_BASE_DIR` | Project root. Holds `config.yaml` and `config.json`. | built |
| `MTFW_FIRMWARE_DIR` | Firmware source directory | built |
| `MTFW_TEMP_DIR` | Build directories | built |
| `MTFW_DATABASE_PATH` | SQLite file | built |
| `MTFW_DEVICES_FILE` | Registry YAML | built; removed by feature 10 |
| `MTFW_MAX_QUEUE_SIZE` | Build queue limit | built; used by the build queue |
| `MTFW_BUILD_WORKERS` | Number of build workers | built; a value above 1 logs a warning, and one build worker runs until #88 |
| `MTFW_BUILD_TIMEOUT_SECONDS` | Build timeout | built |
| `MTFW_CLEANUP_INTERVAL_SECONDS` | Cleanup task interval | built |
| `MTFW_BUILD_MAX_AGE_SECONDS` | Artifact age limit | built |
| `MTFW_ADMIN_PASSWORD_HASH` | Admin bcrypt hash | built |
| `MTFW_SECRET_KEY` | Session and CSRF signing key | built |
| `MTFW_SESSION_MAX_AGE` | Session length | built |
| `MTFW_BUILD_RATE_LIMIT`, `MTFW_LOGIN_RATE_LIMIT` | Rate limits | built (unused); used by feature 08 |
| `MTFW_HOST`, `MTFW_PORT` | Not read | removed by feature 08 |
| `MTFW_DATA_DIR` | One state directory | planned, feature 08 |
| `MTFW_TRUSTED_PROXIES` | Proxies whose `X-Forwarded-For` is trusted | planned, feature 08 |
| `MTFW_INSTALL_TIMEOUT_SECONDS` | Firmware install timeout | planned, feature 09 |
| `MTFW_CACHE_ENABLED`, `MTFW_CACHE_TTL_SECONDS` | Build cache | planned, feature 14 |
| `MTFW_MAX_FLEET_SIZE` | Fleet size limit | planned, feature 14 |
| `GITHUB_TOKEN` | Optional. Raises the GitHub API rate limit for firmware lists | planned, feature 09 |
| `PLATFORMIO_FORCE_COLOR`, `PLATFORMIO_NO_ANSI` | Set by the build for `pio` | built |
| `PLATFORMIO_CORE_DIR` | PlatformIO toolchain directory (Docker volume) | planned, feature 08 |

## Upstream services

| Service | Use | Docs |
|---|---|---|
| GitHub REST API (releases) | Firmware version list and zipball | https://docs.github.com/en/rest/releases/releases |
| api.meshtastic.org | Stable/alpha firmware list | No published docs. Used by `meshtastic/web-flasher`. |
| meshtastic/firmware | Firmware source | https://github.com/meshtastic/firmware |
| meshtastic/protobufs | Enum and config reference | https://github.com/meshtastic/protobufs |

## Support

Operators file problems at https://github.com/Crank-Git/MTFWBuilder/issues.
