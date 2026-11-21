# Vendored Meshtastic modules

The push config page loads these three ES modules (feature push-config, FR-push-config-2).
ADR 0008 records the choice. Each file is an esm.sh build at `target=es2022`, copied
without changes. `tests/test_push_config_page.py` compares the size and the SHA-256 of
each file with the ADR 0008 "Vendor layout" table.

| File | Package | Version | Source URL | SHA-256 | License |
|---|---|---|---|---|---|
| `meshtastic-core-2.6.7.mjs` | `@meshtastic/core` | 2.6.7 | https://esm.sh/@meshtastic/core@2.6.7/es2022/core.bundle.mjs | `81958cacb658d8da946c207ff90db5ab5c0ec07191fce66a3ced379a29e39864` | GPL-3.0-only |
| `meshtastic-transport-web-serial-0.2.5.mjs` | `@meshtastic/transport-web-serial` | 0.2.5 | https://esm.sh/@meshtastic/transport-web-serial@0.2.5/X-ZUBtZXNodGFzdGljL2NvcmU/es2022/transport-web-serial.bundle.mjs | `ecca82a550bac1577fd060d75c8a45ea9fcb54b1b88b4f034ee227a947bed88f` | GPL-3.0-only |
| `meshtastic-transport-web-bluetooth-0.1.5.mjs` | `@meshtastic/transport-web-bluetooth` | 0.1.5 | https://esm.sh/@meshtastic/transport-web-bluetooth@0.1.5/X-ZUBtZXNodGFzdGljL2NvcmU/es2022/transport-web-bluetooth.bundle.mjs | `f9f60e7282b2f7616d862df1dda76a1cc794d3ee8ed05474f3a15a2068071823` | GPL-3.0-only |

GPL-3.0-only is compatible with the GPL-3.0-or-later license of the project (ADR 0004).

The core file imports no other file. Each transport file has one import,
`from "@meshtastic/core"`. The import map in `templates/push_config.html` resolves that
name to the vendored core file, so the page and both transports share one copy of it.

Each file ends with a `sourceMappingURL` comment. The map files are not vendored, so the
browser developer tools report a 404 for them. The page works without them.

## npm packages

| Package | Tarball | Tarball SHA-1 |
|---|---|---|
| `@meshtastic/core` 2.6.7 | https://registry.npmjs.org/@meshtastic/core/-/core-2.6.7.tgz | `42bcc37780081dbfb446b08828d71fe3b3dd1086` |
| `@meshtastic/transport-web-serial` 0.2.5 | https://registry.npmjs.org/@meshtastic/transport-web-serial/-/transport-web-serial-0.2.5.tgz | `118566cded34fe0b2136615f7ca152e9467c01ba` |
| `@meshtastic/transport-web-bluetooth` 0.1.5 | https://registry.npmjs.org/@meshtastic/transport-web-bluetooth/-/transport-web-bluetooth-0.1.5.tgz | `9d7db416de2d60ebbe8a95e8970aacdfd89e21e5` |

Retrieved 2026-11-21. The SHA-1 values and the licenses come from the npm registry.

## Update a module

esm.sh can rebuild a URL when its build version changes. A new hash needs a new review.

1. Download each URL with `curl -sL`.
2. Compare the SHA-256 with the ADR 0008 table. If a hash differs, stop and ask.
3. Copy each file to this directory under its versioned name.
4. Change the import map in `templates/push_config.html`.
5. Update the tables above and the ADR.
