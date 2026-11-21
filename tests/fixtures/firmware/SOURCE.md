# Firmware fixture source

The files in this directory are a subset of the upstream Meshtastic firmware source.
The contract tests read them in place of a full firmware clone.

| Field | Value |
|---|---|
| Repository | https://github.com/meshtastic/firmware |
| Firmware version | `v2.7.26.54e0d8d` |
| Commit | `54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb` (commit date 2026-06-23) |
| Copied | 2026-11-21 |

## Files

The files keep their upstream relative paths. Nothing in them is changed.

Root files:

- `platformio.ini`
- `userPrefs.jsonc`
- `bin/platformio-custom.py`
- `bin/platformio-pre.py`

Variant files, with the variants each file defines:

| File | Architecture | Variants |
|---|---|---|
| `variants/esp32s3/heltec_v4/platformio.ini` | `esp32s3` | `heltec-v4`, `heltec-v4-tft` |
| `variants/esp32s3/heltec_v3/platformio.ini` | `esp32s3` | `heltec-v3` |
| `variants/nrf52840/rak4631/platformio.ini` | `nrf52840` | `rak4631`, `rak4631_dbg` |
| `variants/nrf52840/t-echo/platformio.ini` | `nrf52840` | `t-echo`, `t-echo-inkhud` |
| `variants/rp2040/rpipico/platformio.ini` | `rp2040` | `pico` |
| `variants/rp2040/rak11310/platformio.ini` | `rp2040` | `rak11310` |

Architecture files, which the variant files extend:

- `variants/esp32/esp32-common.ini`
- `variants/esp32s3/esp32s3.ini`
- `variants/nrf52840/nrf52.ini`
- `variants/nrf52840/nrf52840.ini`
- `variants/rp2040/rp2040.ini`
- `src/graphics/niche/InkHUD/PlatformioConfig.ini` (the `inkhud` section of `t-echo-inkhud`)

Source files: every file under `src/` that contains the text `USERPREFS_` at this
firmware version. The contract test scans these files for the keys the firmware reads.

- `src/graphics/draw/UIRenderer.cpp`
- `src/graphics/draw/UIRenderer.h`
- `src/graphics/img/icon.xbm`
- `src/graphics/img/icon_small.xbm`
- `src/graphics/niche/InkHUD/Applets/System/Logo/LogoApplet.cpp`
- `src/graphics/niche/Inputs/TwoButton.cpp`
- `src/graphics/niche/Inputs/TwoButtonExtended.cpp`
- `src/graphics/Screen.cpp`
- `src/graphics/Screen.h`
- `src/input/InputBroker.cpp`
- `src/main.cpp`
- `src/mesh/Channels.cpp`
- `src/mesh/Default.cpp`
- `src/mesh/Default.h`
- `src/mesh/NextHopRouter.cpp`
- `src/mesh/NodeDB.cpp`
- `src/mesh/Router.cpp`
- `src/modules/AdminModule.cpp`
- `src/modules/ExternalNotificationModule.cpp`
- `src/modules/NodeInfoModule.cpp`
- `src/modules/PositionModule.h`
- `src/platform/portduino/SimRadio.cpp`

Enum file: `src/mesh/generated/meshtastic/config.pb.h`, the nanopb header of `config.proto`. The key scan of a
firmware version reads the enum values of the firmware version from it.

## Env list

`envs.json` is not an upstream file. It maps each `[env:…]` name to its architecture
folder (the first folder under `variants/`). It holds all 230 envs of every
`variants/**/*.ini` file at the firmware version above. The registry id test and the fake
`pio` read it.

To make it again, clone the tag with `git clone --depth 1 --branch <version>`. Then read
each `^\[env:([^\]]+)\]` line of each `variants/**/*.ini` file.

## Project config

`project_config.json` is not an upstream file. It is the output of `pio project config --json-output`
(PlatformIO Core 6.2.0) in a copy of this directory, retrieved 2026-11-21. The text `<firmware_dir>`
replaces the absolute path of the copy. The registry tests and the fake `pio` read it.

To make it again, run the command in a copy of this directory. Then replace the path of the copy with
`<firmware_dir>`, and write the JSON with an indent of 1.

## Release list

`releases.json` is the first 10 entries of
`GET https://api.github.com/repos/meshtastic/firmware/releases`, retrieved 2026-11-21. Each
entry keeps only `tag_name`, `name`, `prerelease`, `draft`, `published_at` and
`zipball_url`. It holds a revoked alpha (`v2.8.2.4c89b69`) with the same version numbers as
the newest alpha (`v2.8.2.d5f1619`). The install tests read it in place of the GitHub API.

## Firmware list

`firmware_list.json` is the response of
`GET https://api.meshtastic.org/github/firmware/list`, retrieved 2026-11-21. Each entry keeps
only `id`, `title`, `page_url` and `zip_url`, and `pullRequests` keeps its first 2 entries.
The API has no published docs. `meshtastic/web-flasher` (`types/api.ts`, commit
`6b3b347`) gives the shape `FirmwareReleases`. The response holds no release date and no
revoked alpha. The catalog tests read it in place of the API.

## License

The upstream firmware is GPL-3.0. This project is GPL-3.0-or-later (ADR 0004), so it
can carry these files.

## Refresh

Use the project skill `refresh-fixtures` (`.claude/skills/refresh-fixtures/SKILL.md`).
After a refresh, update the firmware version, the commit and the date in this file.
