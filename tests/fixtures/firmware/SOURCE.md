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

The architecture files that these variant files extend (for example
`variants/rp2040/rp2040.ini`) are not in the fixture.

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

## License

The upstream firmware is GPL-3.0. This project is GPL-3.0-or-later (ADR 0004), so it
can carry these files.

## Refresh

Use the project skill `refresh-fixtures` (`.claude/skills/refresh-fixtures/SKILL.md`).
After a refresh, update the firmware version, the commit and the date in this file.
