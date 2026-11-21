---
paths:
  - "mtfwbuilder/**/*.py"
  - "tests/**/*.py"
  - "templates/**/*.html"
  - "static/js/**/*.js"
---

# Quality rules — reviewed per rule, pass or fail

Default floor:
- No try/except that only re-raises, or logs and continues.
- No `except Exception` that hides the error type the code expects
  (for example `except (json.JSONDecodeError, Exception)`).
- No defensive checks against states the types already exclude.
- No abstraction with a single caller. Inline it until a second caller exists.
- No configuration for a value nothing varies. No setting that nothing reads.
- No dead code and no commented-out code.
- A comment states what the code cannot (see the STE rule). Delete comments that restate
  the code.

Project rules:
- No `print()`. Use `logging`.
- No `shell=True`. Pass an argument list to the subprocess call.
- No secret (PSK, WiFi PSK, MQTT password, admin key) in a log line, a database row, a
  cache entry, an SSE event, or browser storage.
- Every route validates its body with a pydantic model. No `await request.json()` that
  goes to a service without a model.
- A validation error returns 422 with field errors. Never HTTP 200 with `success: false`.
- Every userPrefs key comes from one mapping module, and the contract test covers it.
- Every enum value (region, modem preset, role) comes from `mtfwbuilder/meshtastic_enums.py`
  after Epic 7, never from a literal list in a template.
- Templates insert server text as text. No `innerHTML` with server or user content.
- No script or stylesheet from a CDN. Vendor it under `static/vendor/`.
- A test does not write outside `tmp_path`.
