# 0008 — Push config with `@meshtastic/core` 2.6.7 and its two browser transports

Date: 2026-11-21
Status: accepted. Owner ruling: https://github.com/Crank-Git/MTFWBuilder/issues/70#issuecomment-7067061600

## Context

Push config (feature 15) talks to a device from the page over Web Serial or Web Bluetooth. The project has no JavaScript build step (ADR 0002), so the library must load in the browser as prebuilt ES modules from `static/vendor/`. FR-push-config-1 names two candidates. `meshtastic/js`, the old source of `@meshtastic/core`, is archived. The spike read the registry pages, the published package files and the upstream source on 2026-11-21.

### Comparison

| Point | `@meshtastic/core` + `@meshtastic/transport-web-serial` + `@meshtastic/transport-web-bluetooth` | `meshtastic-sdk` JS |
|---|---|---|
| Latest version | npm: core `2.6.7`, web-serial `0.2.5`, web-bluetooth `0.1.5`, all published 2025-09-11. JSR: core `2.6.6` (2025-08-08), web-serial `0.2.5` (2025-08-21), web-bluetooth `0.1.2` (2025-06-06). | Release `v0.2.0` (2026-10-03). The `js` target landed in PR #215 on 2026-11-20. No release carries it. |
| Maintenance | `meshtastic/web` removed `packages/core` on 2026-06-15 (commits `dc9749c04`, `cffa7f28e`). No version after 2.6.7. | Active. The web client now compiles it from source as the private package `@meshtastic/sdk-kmp`. |
| License | `GPL-3.0-only` in each npm `package.json`. Compatible with GPL-3.0-or-later (ADR 0004). | `GPL-3.0`. Compatible. |
| Prebuilt ES module | npm `dist/mod.js` is ESM, but it imports `os`, `path`, `util` and `crc`. esm.sh serves a self-contained build (URLs below). | None. The README says the JS klibs "are compiled into the consumer's own bundle". A build needs JDK 21 and Gradle. |
| Web Serial | `TransportWebSerial.create(baudRate?)`, `TransportWebSerial.createFromPort(port, baudRate?)` | No browser transport in the SDK. The host supplies a `RadioTransport`. |
| Web Bluetooth | `TransportWebBluetooth.create()`, `TransportWebBluetooth.createFromDevice(device)` | `transport-ble` lists a JS target, but `@meshtastic/sdk-kmp` says it "has no `js` target in the published artifacts". |
| Admin API | `beginEditSettings()` (TypeScript `private`, callable at runtime), `setConfig(config)`, `setModuleConfig(moduleConfig)`, `setChannel(channel)`, `setOwner(owner)`, `commitEditSettings()`. Each returns a `Promise<number>` that resolves on the device acknowledgement. | Kotlin coroutine API. No ES module to call it from. |
| Read config | `configure()` sends `want_config`. The device answers through `events.onConfigPacket`, `onModuleConfigPacket`, `onChannelPacket`, `onMyNodeInfo` and `onNodeInfoPacket`. `getOwner()` answers through `onUserPacket`. | Not evaluated: no ES module. |
| Managed mode | `Config.security.isManaged` in the `onConfigPacket` message with `payloadVariant.case === "security"`. | Not evaluated. |
| Size | esm.sh core build 255,589 bytes (80,430 gzip). Transport builds 3,154 and 3,533 bytes. | Not measured: no build. |
| Protobufs | The core build carries `@meshtastic/protobufs` 2.7.8 and `@bufbuild/protobuf` 2.8.0. Firmware stable is 2.7.26, alpha is 2.8.2, the reference is protobufs `v2.8.3`. | Follows upstream protobufs. |

`setConfig` sends `begin_edit_settings` first when no edit is open. `setModuleConfig`, `setChannel` and `setOwner` do not. Source: `dist/mod.js` lines 12994–13030 and 13153–13168 of the npm core 2.6.7 package.

## Decision

The project uses `@meshtastic/core` 2.6.7, `@meshtastic/transport-web-serial` 0.2.5 and `@meshtastic/transport-web-bluetooth` 0.1.5 from npm. `meshtastic-sdk` has no JavaScript artifact that a page can load without a build. The npm versions are newer than the JSR versions.

### Vendor layout

#72 vendors three ES modules. Each comes from esm.sh, at `target=es2022`:

| File in `static/vendor/meshtastic/` | Source URL | Size | SHA-256 |
|---|---|---|---|
| `meshtastic-core-2.6.7.mjs` | https://esm.sh/@meshtastic/core@2.6.7/es2022/core.bundle.mjs | 255,589 | `81958cacb658d8da946c207ff90db5ab5c0ec07191fce66a3ced379a29e39864` |
| `meshtastic-transport-web-serial-0.2.5.mjs` | https://esm.sh/@meshtastic/transport-web-serial@0.2.5/X-ZUBtZXNodGFzdGljL2NvcmU/es2022/transport-web-serial.bundle.mjs | 3,154 | `ecca82a550bac1577fd060d75c8a45ea9fcb54b1b88b4f034ee227a947bed88f` |
| `meshtastic-transport-web-bluetooth-0.1.5.mjs` | https://esm.sh/@meshtastic/transport-web-bluetooth@0.1.5/X-ZUBtZXNodGFzdGljL2NvcmU/es2022/transport-web-bluetooth.bundle.mjs | 3,533 | `f9f60e7282b2f7616d862df1dda76a1cc794d3ee8ed05474f3a15a2068071823` |

The first URL is the target of `https://esm.sh/@meshtastic/core@2.6.7?standalone&target=es2022`. The other two are the targets of `?standalone&target=es2022&external=@meshtastic/core`. The core file has no `import`. Each transport file has one import, `from "@meshtastic/core"`.

The page resolves `@meshtastic/core` with an import map before its module script:

```html
<script type="importmap">
  {"imports": {"@meshtastic/core": "/static/vendor/meshtastic/meshtastic-core-2.6.7.mjs"}}
</script>
```

The import map gives the page and both transports one copy of the core module. Chrome and Edge support import maps from version 89. Web Serial needs the same browsers.

#72 does these steps:

1. Download each URL with `curl -sL`.
2. Compare the SHA-256 with the table. If a hash differs, stop and ask.
3. Copy each file to `static/vendor/meshtastic/` under the name in the table, without changes.
4. Add the three files and the npm tarball SHA-1 values to `static/vendor/meshtastic/README.md`.

| Package | Tarball SHA-1 |
|---|---|
| `@meshtastic/core` 2.6.7 | `42bcc37780081dbfb446b08828d71fe3b3dd1086` |
| `@meshtastic/transport-web-serial` 0.2.5 | `118566cded34fe0b2136615f7ca152e9467c01ba` |
| `@meshtastic/transport-web-bluetooth` 0.1.5 | `9d7db416de2d60ebbe8a95e8970aacdfd89e21e5` |

## Consequences

- The page loads three files, not one. The owner accepted this layout, and FR-push-config-2 now says "one prebuilt ES module per package, joined by an import map".
- esm.sh can rebuild a URL when its build version changes. The SHA-256 table is the check. A changed hash needs a new review.
- Risk: upstream published nothing after 2.6.7 and removed the source on 2026-06-15. The project pins 2.6.7. A protocol change after protobufs 2.7.8 needs a different library or a fork.
- The bundled protobufs 2.7.8 have no `ModuleConfig` fields `statusmessage` (14), `traffic_management` (15), `tak` (16) and `mesh_beacon` (17). By the owner ruling, FR-push-config-10 no longer covers `status_message` and `traffic_management`. A newer library can add them again.
- The bundled protobufs 2.7.8 also have no `Config` fields `display.use_long_node_name`, `display.enable_message_bubbles`, `lora.fem_lna_mode`, `lora.serial_hal_only`, `lora.transparent_mode` and `security.packet_signature_policy`.
- `beginEditSettings` is private in the TypeScript types only. The page calls it directly, or sends `setConfig` first in each transaction.
- When `meshtastic-sdk` publishes a JS release that a page can load without a build, a new ADR can replace this one.

## Sources

Retrieved 2026-11-21:

- https://registry.npmjs.org/@meshtastic%2Fcore, https://registry.npmjs.org/@meshtastic%2Ftransport-web-serial, https://registry.npmjs.org/@meshtastic%2Ftransport-web-bluetooth
- https://jsr.io/@meshtastic, https://api.jsr.io/scopes/meshtastic/packages
- https://github.com/meshtastic/meshtastic-sdk (README, release `v0.2.0`, PR #215)
- https://github.com/meshtastic/web/tree/main/packages/sdk-kmp (README)
- https://github.com/meshtastic/protobufs (`meshtastic/module_config.proto` and `meshtastic/config.proto` at `v2.7.8` and `v2.8.3`)
- https://github.com/esm-dev/esm.sh (README: `?standalone`, `?external`)
- https://github.com/mdn/browser-compat-data (`html.elements.script.type.importmap`)
