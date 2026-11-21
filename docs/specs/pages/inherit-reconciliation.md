---
id: inherit-reconciliation
title: Inherit reconciliation and research
---

# Inherit reconciliation and research

`/project-inherit` wrote this page on 2026-11-21. It records what the existing documents
claim, what the code does, and what the upstream research found. The code is the fact.
A document is a claim.

## Sources harvested

| Source | Claims to cover | Date |
|---|---|---|
| `README.md` | Features, 62 variants, architecture, API, Docker, security, troubleshooting | 2026-03-25 (`461d304`) |
| `CONTRIBUTING.md` | Setup, layout, how to add a variant, code style | 2026-03-25 |
| `pyproject.toml` | v2.0.0, MIT, Python ≥3.10, dependencies | 2026-03-25 |
| `requirements.txt`, `docker.env.example` | v1 Flask stack | 2025-06 |
| `config.yaml.example`, `config.json.example` | Settings | 2026-03-25 / 2025-06 |
| `docker-compose*.yml`, `Dockerfile`, `nginx/nginx.conf.example`, `docker-deploy.sh` | Deploy | 2026-03-25 |
| Git log (21 commits), PRs #1 and #2 | v1 (June 2025), v2 rewrite in 6 phases plus a review (March 2026), registry refresh to 150 | 2025-06 → 2026-03 |
| GitHub issues | None | — |
| `CLAUDE.md`, `AGENTS.md`, `.claude/`, `docs/`, ADRs, CI | None | — |

## Confirmed (document and code agree)

- FastAPI + uvicorn backend, routers and services layout (README Architecture).
- 106 tests (39 + 26 + 20 + 14 + 7).
- bcrypt password hash, auto-hash of a plaintext password, signed cookie session.
- Rate limits: login 10/min, build 5/min.
- Zip-slip check on the firmware source download.
- No `shell=True` in subprocess calls.
- Upload size limit of 64 KB.
- ESP32 factory artifact preferred on download.

## Contradicted (the code wins)

The list is in `spec.md` § Risks & open questions, items 1–13.

## Undocumented (the code does it, no document says so)

- The build starts only when a client opens the progress stream, and a closed stream
  cancels it.
- The frontend still calls v1 URLs. Preview, download, build, update and system info do
  not work from the pages.
- The generator writes about 20 userPrefs keys that upstream does not read (MQTT, WiFi,
  network protocols, fixed position, smart position, OEM, device name, admin keys).
- Scrub runs on the success path only.
- `config.yaml` is rewritten at startup (password hash, secret key), also by the tests.
- The default admin password `meshtastic` persists after first start with no warning.
- The SQLite tables are created and never used.
- The `Secure` cookie flag breaks sign-in on plain HTTP.
- The upstream build prints every `-D` flag. The progress stream sends the last 100 log
  lines, so it sends secrets.
- Build ids are predictable, and artifact downloads have no owner check.
- Docker volumes mount paths the app does not use, and state in `/app` is lost when the
  container is recreated.
- nginx does not disable buffering for the progress stream.

## Upstream research summary (2026-11-21)

**Firmware releases.** Stable `v2.7.26.54e0d8d` (2026-06-24) is what
`releases/latest` returns. Alpha `v2.8.2.d5f1619` (2026-11-04). No 2.8 stable yet.
Firmware 2.8 moves ESP32 to the pioarduino platform (arduino-esp32 3.3, ESP-IDF 5.5).
Source: https://github.com/meshtastic/firmware/releases

**Artifact names.** `bin/platformio-pre.py` sets `PROGNAME = firmware-<env>-<version>`
(PR #8248, December 2025). Per architecture:
- ESP32 family: `.bin`, `.factory.bin`, `littlefs-<env>-<ver>.bin`, `.mt.json`.
- nRF52: `.uf2`, `.hex`, `-ota.zip`.
- nRF54L15: `.hex`, `-ota.zip`.
- RP2040 / RP2350: `.uf2`.
- STM32: `.bin`.

**Variants.** 281 envs at `develop@fe89ac1`, 214 at v2.7.20. By architecture:
esp32s3 105, nrf52840 89, esp32 36, native 15, rp2040 11, esp32c3 6, stm32 6, rp2350 5,
nrf54l15 4, esp32c6 3, esp32s2 1. Against the registry: 79 ids valid, 71 ids are
directory names, 202 envs missing (67 new since March). Variant `platformio.ini` files
carry `custom_meshtastic_*` metadata and `board_level`.

**userPrefs keys.** 105 keys in `userPrefs.jsonc` at `develop@fe89ac1`, 60 at v2.7.20.
None removed. Values are factory defaults only.

**Router roles.** A userPrefs role of `ROUTER`, `ROUTER_LATE` or `LOST_AND_FOUND` falls
back to `CLIENT` (`src/mesh/NodeDB.cpp:1180-1188`).

**Admin keys.** The key is `USERPREFS_USE_ADMIN_KEY_<n>` with a 32-byte array value
(`src/mesh/NodeDB.cpp:98-99`). `USERPREFS_ADMIN_KEY_<n>` appears in comments only.

**Protobufs v2.8.3** (2026-11-17): 37 regions, 17 modem presets, 13 roles (2
deprecated). New module configs: status message, traffic management, TAK, mesh beacon.

**Licenses.** Every Meshtastic repository (firmware, protobufs, python, web, js,
meshtastic-sdk) is GPL-3.0. esptool-js and ESP Web Tools are Apache-2.0.

**Ecosystem.** The official Web Flasher flashes ESP32 through esptool-js over Web Serial
and gives UF2 files for drag-and-drop. No other project gives a web interface for
custom-default builds. Community builders found: `skgsergio/meshtastic-firmware-builder`,
`SMLunchen/mh_firmware-builder`, `dmitryplohotnyuk/meshmemo-firmware-builder` (all
command-line, 0–1 stars).

## Registry id map

Each wrong `id` in `devices/variants.yaml` and the env name(s) that replace it.
Verified against `develop@fe89ac1`. No env was removed between v2.7.20 and
`develop@fe89ac1`. FR-build-pipeline-repair-20 uses this map.

| local id | upstream env(s) |
|---|---|
| `Dongle_nRF52840-pca10059-v1` | `pca10059_diy_eink` |
| `MS24SF1` | `ms24sf1` |
| `esp32-s3-pico` | `ESP32-S3-Pico` |
| `mesh-tab` | `mesh-tab-3-2-TN-resistive`, `mesh-tab-3-2-IPS-resistive`, `mesh-tab-3-5-IPS-resistive`, `mesh-tab-3-5-TN-resistive`, `mesh-tab-3-2-IPS-capacitive`, `mesh-tab-3-5-IPS-capacitive`, `mesh-tab-4-0-IPS-capacitive` |
| `dls_Minimesh_Lite` | `minimesh_lite` |
| `nugget_s2_lora` | `nugget-s2-lora` |
| `nugget_s3_lora` | `nugget-s3-lora` |
| `ELECROW-ThinkNode-M1` | `thinknode_m1`, `thinknode_m1-inkhud` |
| `ELECROW-ThinkNode-M2` | `thinknode_m2` |
| `ELECROW-ThinkNode-M3` | `thinknode_m3` |
| `ELECROW-ThinkNode-M4` | `thinknode_m4` |
| `ELECROW-ThinkNode-M5` | `thinknode_m5`, `thinknode_m5-inkhud` |
| `ELECROW-ThinkNode-M6` | `thinknode_m6` |
| `elecrow_panel` | `elecrow-adv-24-28-tft`, `elecrow-adv-35-tft`, `elecrow-adv1-43-50-70-tft` |
| `ec_catsniffer` | `catsniffer` |
| `hackerboxes_esp32_io` | `hackerboxes-esp32-io` |
| `hackerboxes_esp32c3_oled` | `hackerboxes-esp32c3-oled` |
| `heltec_esp32c3` | `heltec-ht62-esp32c3-sx1262` |
| `heltec_hru_3601` | `heltec-hru-3601` |
| `heltec_mesh_node_t096` | `heltec-mesh-node-t096` |
| `heltec_mesh_node_t114` | `heltec-mesh-node-t114` |
| `heltec_mesh_node_t114-inkhud` | `heltec-mesh-node-t114-inkhud` |
| `heltec_mesh_pocket` | `heltec-mesh-pocket-5000`, `heltec-mesh-pocket-5000-inkhud`, `heltec-mesh-pocket-10000`, `heltec-mesh-pocket-10000-inkhud` |
| `heltec_mesh_solar` | `heltec-mesh-solar`, `heltec-mesh-solar-eink`, `heltec-mesh-solar-inkhud`, `heltec-mesh-solar-oled`, `heltec-mesh-solar-tft` |
| `heltec_v1` | `heltec-v1` |
| `heltec_v2` | `heltec-v2_0` |
| `heltec_v2.1` | `heltec-v2_1` |
| `heltec_v3` | `heltec-v3` |
| `heltec_v4` | `heltec-v4`, `heltec-v4-tft` |
| `heltec_vision_master_e213` | `heltec-vision-master-e213`, `heltec-vision-master-e213-inkhud` |
| `heltec_vision_master_e290` | `heltec-vision-master-e290`, `heltec-vision-master-e290-inkhud` |
| `heltec_vision_master_t190` | `heltec-vision-master-t190` |
| `heltec_wireless_bridge` | `heltec-wireless-bridge` |
| `heltec_wireless_paper` | `heltec-wireless-paper`, `heltec-wireless-paper-inkhud` |
| `heltec_wireless_paper_v1` | `heltec-wireless-paper-v1_0` |
| `heltec_wireless_tracker` | `heltec-wireless-tracker` |
| `heltec_wireless_tracker_V1_0` | `heltec-wireless-tracker-V1-0` |
| `heltec_wireless_tracker_v2` | `heltec-wireless-tracker-v2` |
| `heltec_wsl_v2.1` | `heltec-wsl-v2_1` |
| `heltec_wsl_v3` | `heltec-wsl-v3` |
| `t5s3_epaper` | `t5s3_epaper_inkhud`, `t5s3-epaper-v1`, `t5s3-epaper-v2` |
| `tbeam_v07` | `tbeam0_7` |
| `tlora_c6` | `tlora-c6` |
| `tlora_t3s3_epaper` | `tlora-t3s3-epaper`, `tlora-t3s3-epaper-inkhud` |
| `tlora_t3s3_v1` | `tlora-t3s3-v1` |
| `tlora_v1` | `tlora-v1` |
| `tlora_v2` | `tlora-v2` |
| `tlora_v2_1_16` | `tlora-v2-1-1_6`, `sugarcube` |
| `tlora_v2_1_16_tcxo` | `tlora-v2-1-1_6-tcxo` |
| `tlora_v2_1_18` | `tlora-v2-1-1_8` |
| `tlora_v3_3_0_tcxo` | `tlora-v3-3-0-tcxo` |
| `link32_s3_v1` | `link32-s3-v1` |
| `m5stack_cardputer_adv` | `m5stack-cardputer-adv` |
| `m5stack_core` | `m5stack-core` |
| `m5stack_coreink` | `m5stack-coreink` |
| `m5stack_cores3` | `m5stack-cores3` |
| `m5stack_unitc6l` | `m5stack-unitc6l` |
| `MakePython_nRF52840_eink` | `makerpython_nrf52840_sx1280_eink` |
| `MakePython_nRF52840_oled` | `makerpython_nrf52840_sx1280_oled` |
| `muzi_base` | `muzi-base` |
| `nibble_esp32` | `nibble-esp32` |
| `nibble_rp2040` | `nibble-rp2040` |
| `rak3401_1watt` | `rak3401-1watt`, `rak_wismesh_repeater_mini_hp` |
| `rak4631_epaper` | `rak4631_eink` |
| `rak4631_epaper_onrxtx` | `rak4631_eink_onrxtx` |
| `rpipico` | `pico` |
| `rpipico-slowclock` | `pico_slowclock` |
| `rpipico2` | `pico2` |
| `rpipico2w` | `pico2w` |
| `rpipicow` | `picow` |
| `seeed_xiao_s3` | `seeed-xiao-s3` |

