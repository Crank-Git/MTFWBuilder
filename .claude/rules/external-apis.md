---
paths:
  - "mtfwbuilder/**/*.py"
  - "static/js/**/*.js"
  - "tests/**/*.py"
  - "docs/specs/**/*.md"
---

# External APIs — read the docs or the source, never assume

Before you write code against any interface below, confirm the operation, the parameters,
the response shape, the error cases and the limits from its documentation or its source,
at the version this project uses. Cite the URL and version in the PR body. If the
documentation contradicts the spec, stop and ask. Do not reconcile by a guess.

Authoritative sources, in order:
1. The tool's own interface: `pio --help`, the shipped type definitions, the upstream
   source file.
2. The official documentation at the pinned version.
3. The vendor's own examples.

A blog post, a forum answer, another project's code, or memory is not authoritative.

| Interface | Version | Source |
|---|---|---|
| Meshtastic firmware (userPrefs keys, artifact names, variant metadata) | stable `v2.7.26.54e0d8d`; alpha `v2.8.2.d5f1619` | https://github.com/meshtastic/firmware — `userPrefs.jsonc`, `bin/platformio-custom.py`, `bin/platformio-pre.py`, `src/mesh/NodeDB.cpp`, `variants/**/platformio.ini` |
| Meshtastic protobufs (enums, config, AdminMessage) | `v2.8.3` | https://github.com/meshtastic/protobufs |
| `meshtastic` python package (channel URL, CLI YAML) | 2.7.x | https://github.com/meshtastic/python, https://meshtastic.org/docs/software/python/cli/ |
| PlatformIO Core | 6.2.0 | https://docs.platformio.org/en/stable/core/index.html |
| GitHub REST releases API | 2022-11-28 | https://docs.github.com/en/rest/releases/releases |
| api.meshtastic.org firmware list | unversioned | No published docs. Read `meshtastic/web-flasher` source. Handle failure (feature 09 falls back to GitHub). |
| esptool-js | 0.7.x | https://github.com/espressif/esptool-js |
| Web Serial API | — | https://developer.mozilla.org/en-US/docs/Web/API/Web_Serial_API |
| `@meshtastic/core`, `@meshtastic/transport-web-serial`, `@meshtastic/transport-web-bluetooth` | 2.6.7, 0.2.5, 0.1.5 (ADR 0008) | https://registry.npmjs.org/@meshtastic%2Fcore, the npm package `dist/mod.d.ts` |
| htmx, htmx SSE extension | 2.0.x, 2.2.x | https://htmx.org/docs/, https://htmx.org/extensions/sse/ |

Upstream changes without notice. When code depends on an upstream file, add or update
the fixture in `tests/fixtures/firmware/` so a test fails when upstream changes.
