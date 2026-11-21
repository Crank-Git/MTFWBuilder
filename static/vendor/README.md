# Vendored frontend libraries

The pages load these files from the application host. The pages load no script or
stylesheet from a CDN (FR-frontend-htmx-16). Each file name carries the library version.

Each file is a copy of the npm package file, without changes. The npm registry
`dist.shasum` (SHA-1) of each package tarball was verified before the copy.

| File | Package | Version | Package file | License |
|---|---|---|---|---|
| `htmx-2.0.11.min.js` | `htmx.org` | 2.0.11 | `dist/htmx.min.js` | 0BSD |
| `htmx-ext-sse-2.2.4.min.js` | `htmx-ext-sse` | 2.2.4 | `dist/sse.min.js` | 0BSD |
| `bootstrap-5.3.9.min.css` | `bootstrap` | 5.3.9 | `dist/css/bootstrap.min.css` | MIT |
| `bootstrap-5.3.9.bundle.min.js` | `bootstrap` | 5.3.9 | `dist/js/bootstrap.bundle.min.js` | MIT |
| `bootstrap-icons-1.13.2/bootstrap-icons.min.css` | `bootstrap-icons` | 1.13.2 | `font/bootstrap-icons.min.css` | MIT |
| `bootstrap-icons-1.13.2/fonts/bootstrap-icons.woff2` | `bootstrap-icons` | 1.13.2 | `font/fonts/bootstrap-icons.woff2` | MIT |
| `bootstrap-icons-1.13.2/fonts/bootstrap-icons.woff` | `bootstrap-icons` | 1.13.2 | `font/fonts/bootstrap-icons.woff` | MIT |
| `esptool-js-0.7.1/bundle.js` | `esptool-js` | 0.7.1 | `bundle.js` | Apache-2.0 |
| `esptool-js-0.7.1/LICENSE` | `esptool-js` | 0.7.1 | `LICENSE` | Apache-2.0 |

The icon stylesheet finds its fonts at the relative path `fonts/`. Keep the two together
in one directory.

`htmx-ext-sse` 2.2.4 declares the dependency `htmx.org ^2.0.2`. htmx 2.0.11 meets it.

## Sources

| Package | Tarball | Tarball SHA-1 |
|---|---|---|
| `htmx.org` 2.0.11 | https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz | `a85363c4c5af94cd634df04c4b83d06dd0e91aa7` |
| `htmx-ext-sse` 2.2.4 | https://registry.npmjs.org/htmx-ext-sse/-/htmx-ext-sse-2.2.4.tgz | `56c131924e57c862f42a1a963949633815fd4192` |
| `bootstrap` 5.3.9 | https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.9.tgz | `08e4c0e371fc43ab2c501cd2d3be445739cd8b13` |
| `bootstrap-icons` 1.13.2 | https://registry.npmjs.org/bootstrap-icons/-/bootstrap-icons-1.13.2.tgz | `5f77e89f4bc80940185a3f1dab1b4878b9119b9c` |
| `esptool-js` 0.7.1 | https://registry.npmjs.org/esptool-js/-/esptool-js-0.7.1.tgz | `493dccb5f33f35336e9217c38ed838611471e5a4` |

Retrieved 2026-11-21. Documentation: https://htmx.org/docs/,
https://htmx.org/extensions/sse/, https://getbootstrap.com/docs/5.3/.

`esptool-js` 0.7.1 `bundle.js` is one ES module. It holds its dependencies `pako` and
`atob-lite`, and it imports no other file. It exports `ESPLoader` and `Transport`. The
flash panel loads it with `import()` from this host only (FR-browser-flashing-1).
Documentation: https://github.com/espressif/esptool-js (0.7.1, 2026-11-02).

The Bootstrap files end with a `sourceMappingURL` comment. The map files are not
vendored, so the browser developer tools report a 404 for them. The pages work without
them.

## Update a library

1. Read the package versions: `curl -s https://registry.npmjs.org/<package>`.
2. Download the tarball and verify `dist.shasum`.
3. Copy the package file under the new versioned name and delete the old file.
4. Change the path in `templates/includes/base.html`. For esptool-js, change the path in
   `templates/partials/flash_panel.html`.
5. Update the tables above.
