# 0002 — Server-rendered pages with htmx

Date: 2026-11-21
Status: accepted

## Context

The v2 rewrite loaded htmx but kept the v1 inline scripts, and the pages call v1 URLs.
The frontend must be rebuilt. The options were: keep vanilla JavaScript, use htmx, or
write a separate single-page app. The owner chose in the `/project-inherit` interview.

## Decision

Pages are Jinja2 templates. htmx and its SSE extension handle forms and the progress
stream. Routes return HTML fragments for `HX-Request` and JSON otherwise. Browser-only
work (Web Serial flashing, push config) uses small vendored ES modules.

## Consequences

- No JavaScript build step and no `node_modules`.
- The JSON API stays for automation.
- Interactive device work (features 13 and 15) needs hand-written modules around
  vendored libraries.
