# HTTP API

Every API route is under `/api/v1`. The running app serves the OpenAPI documentation at
`/docs`, for example `http://localhost:5000/docs`. This page adds the behavior that the
OpenAPI schema does not show.

## Routes

- `POST /api/v1/generate` — Generate config from JSON
- `POST /api/v1/preview` — Preview config without downloading
- `POST /api/v1/download` — Download `userPrefs.jsonc`
- `POST /api/v1/preview-userprefs` — Read an uploaded `userPrefs.jsonc`, sent as the multipart field `userPrefs`.
  Returns `content`, `line_count`, `checks` (five key checks) and `filename`. Returns 413 for a file larger than
  64 KB. Returns 422 when the file has no name or is not UTF-8 text.
- `GET /api/v1/userprefs/keys?version=<tag>` — The userPrefs keys (`keys`) and the `config.proto` enum values
  (`enum_values`) that a firmware version reads, from a scan of its firmware source. Without `version`, the result
  is for the default firmware version. Returns 404 when the firmware version is not installed.
- `POST /api/v1/import/channel-url` — Decode a channel URL `{"url": "https://meshtastic.org/e/#…"}` or an
  `https://meshtastic.org/e/?add=true#…` URL. Returns `channels` (`index`, `name`, `psk` in base64, `default_key`,
  `role`, `uplink_enabled`, `downlink_enabled`, `position_precision`), `lora` (`region`, `modem_preset` and the other
  LoRa settings), `add` and `warnings`. A URL with more than 8 channels gives the first 8 and a warning. Returns 422
  with "This is not a valid Meshtastic channel URL." when the URL does not decode. The server keeps no copy of the URL.
- `POST /api/v1/import/cli-yaml` — Read a `meshtastic --export-config` file, sent as the multipart field `file`.
  Returns `config` (the form fields that have a userPrefs key, with the form field names), `not_applied` (the dotted
  path in the file of each other field, for example `module_config.serial`) and `warnings` (the reason for each field
  that the form rejects). Returns 413 for a file larger than 64 KB. Returns 422 with "This is not a valid Meshtastic
  export file." when `yaml.safe_load` does not read the file or the file holds no mapping. The server keeps no copy of
  the file.
- `POST /api/v1/export/channel-url` — Encode the channels and the LoRa settings of a config, with the
  `/api/v1/generate` body. Returns `url` (a `https://meshtastic.org/e/#…` channel URL) and `qr_svg` (an SVG QR code
  of the URL). The URL holds the channel slots that `channels_to_write` counts. An empty field takes the firmware
  default: PSK default key 1, position precision 13, hop limit 3. Returns 422 when `channels_to_write` is 0.
- `POST /api/v1/build-firmware` — Start firmware build
- `GET /api/v1/build-progress/{id}` — SSE build progress stream
- `GET /api/v1/builds/{id}` — Build status of a build: `build_id`, `status`, `message`, `progress`, `error`, `download_url`
- `POST /api/v1/builds/{id}/cancel` — Cancel a queued or running build
- `GET /api/v1/builds/{id}/artifacts` — Artifact list of a build: `name`, `size`, `kind`
- `GET /api/v1/builds/{id}/artifacts/{name}` — Download one artifact from the artifact list
- `GET /api/v1/download-firmware/{id}` — Redirects (308) to the factory or primary artifact, for one release
- `GET /api/v1/system-info` — The default firmware version (`version`, `last_updated`) and the list
  `firmware_versions`, read from the `firmware_sources` table. Without a firmware source, `version`
  is `Not installed`.
- `GET /api/v1/firmware` — Installed firmware versions, the newest first: `version`, `channel`
  (`stable`, `alpha` or `pinned`), `installed_at`, `size_bytes`, `status`
- `GET /api/v1/firmware/available` (admin) — Upstream firmware versions, the newest first:
  `source` (`api.meshtastic.org` or `github`), and the lists `stable` and `alpha`. Each
  entry has `tag`, `title`, `date` and `revoked`. Returns 502 when both upstreams fail.
- `POST /api/v1/firmware/install` (admin) — Install by `{"channel": "stable"}`, `{"channel": "alpha"}`
  or `{"tag": "<tag>"}`. Returns 202 with `install_id`, `progress_url` and `warning`, and 409 when
  the firmware version is installed. `warning` is not empty when the upstream firmware list marks
  the exact tag as revoked. One install runs at a time, and the next install waits in `queued`.
- `GET /api/v1/firmware/install/{install_id}` (admin) — Install status of an install: `install_id`,
  `version`, `status`, `error`. Returns 404 for an unknown install.
- `GET /api/v1/firmware/install/{install_id}/progress` (admin) — SSE install progress stream
- `POST /api/v1/update-firmware` (admin) — Install the newest stable firmware version and wait for the install
  to end. Returns 200 with `success` and `message`. Returns 502 when the upstream firmware list gives no stable
  firmware version. Returns 500 when the install fails: the response gives no reason, and the server log has it.
- `DELETE /api/v1/firmware/{version}` (admin) — Remove an installed firmware version. Returns 204,
  and 409 while a build uses it.
- `GET /api/v1/variants?version=&q=&arch=&experimental=` — Registry entries of a firmware version, in env
  order. Without `version`, the registry of the default firmware version. `q` matches the display name, the
  env or the manufacturer without case and punctuation. Give `arch` once for each architecture to keep.
  Variants with support level `extra` show only with `experimental=true`. Each entry has the registry
  fields and `image_url`, which is null when the firmware source does not hold the image file. Returns 404
  for a `version` that is not installed. An htmx request (header `HX-Request`) gets the device picker
  list of the build page as HTML, grouped by manufacturer.
- `GET /api/v1/variants/{env}/image?version=` — The SVG or PNG device image from the firmware source.
  Returns 404 when the firmware source does not hold the image file named in `custom_meshtastic_images`.
- `GET /api/v1/profiles` — Every profile in name order, without secrets. A profile has `id`, `name`, `config`,
  `must_reenter`, `stores_secrets`, `warning`, `created_at` and `updated_at`.
- `POST /api/v1/profiles` — Save `{"name": "<name>", "config": {...}, "stores_secrets": false}` as a
  profile. Returns 201 with the profile, and 409 when another profile has the name. Without
  `stores_secrets`, the save removes each secret: the channel PSKs, the WiFi PSK, the MQTT password, the
  admin keys and the mesh beacon offer PSK. `must_reenter` names each removed field.
- With `"stores_secrets": true`, the profile keeps the secrets encrypted with Fernet. The key comes from
  `secret_key` through HKDF-SHA256. This save needs an admin session and the CSRF token, else it returns
  403. No response holds the encrypted secrets. The responses of `POST`, `GET` one and `PUT` send
  `Cache-Control: no-store`, because they can hold the stored secrets.
- `GET /api/v1/profiles/{id}` — The profile. Returns 404 for an unknown profile. Only an admin session
  gets the stored secrets in `config`. Without one, `must_reenter` names them, and `warning` is
  "Sign in as admin to load the stored secrets."
- If `secret_key` changes, the stored secrets do not decrypt. The response then names them in
  `must_reenter` and sets `warning`.
- `PUT /api/v1/profiles/{id}` — Rename with `{"name": ...}`, replace the config with `{"config": {...}}`,
  set the option with `{"stores_secrets": ...}`, or more of them. A false `stores_secrets` deletes the
  stored secrets. A change that stores or removes secrets needs an admin session and the CSRF token, else it
  returns 403. A false `stores_secrets` and a new config of a profile that stores secrets are such changes.
  Returns 409 when another profile has the new name. Returns 422 for `{"stores_secrets": true}` without a
  config, when the profile stores no secrets.
- `DELETE /api/v1/profiles/{id}` — Delete the profile. Returns 204.
- `GET /api/v1/profiles/{id}/export` — The profile as a JSON file with `name`, `config` and
  `must_reenter`, without secrets.
- `GET /api/v1/fleets` — Every fleet in the order of creation, with its fleet devices, without admin keys.
- `POST /api/v1/fleets` — Create a fleet from a profile with `{"name": ..., "profile_id": ...,
  "firmware_version": null, "devices": [...]}`. A null `firmware_version` selects the default firmware
  version when a fleet run starts. Returns 201 with the fleet. Returns 422 when the profile does not exist or
  the fleet holds more than `MTFW_MAX_FLEET_SIZE` devices (default 50).
- `GET /api/v1/fleets/{id}` — The fleet. Only an admin session gets the admin key of each fleet device.
  `has_admin_key` tells if a fleet device has one.
- `PUT /api/v1/fleets/{id}` — Change the `name`, the `profile_id`, the `firmware_version` or the `devices`
  of the fleet. Returns the errors of `POST /api/v1/fleets`.
- `DELETE /api/v1/fleets/{id}` — Delete the fleet with its fleet devices and fleet runs. Returns 204.
- `PUT /api/v1/fleets/{id}/devices` — Replace every fleet device with `{"devices": [{...}, ...]}`, the rows
  of the fleet device table. A row without an `admin_key` keeps the saved admin key of its label.
- `POST /api/v1/fleets/{id}/devices/csv` — Add a fleet device for each line of `{"csv": "<text>"}` after
  the header `label,variant,long_name,short_name,role,lat,lon,alt,admin_key`. The rows go after the saved
  fleet devices.

The two fleet device routes return 404 for an unknown fleet. Each returns 422 with every row error and
then saves no row. A row error has `row`, `field` and `msg`. Row 1 is the first fleet device: the first
table row, or the line after the CSV header. A duplicate label, a variant that is not in the firmware
version of the fleet, and a router role are row errors. The page `/fleets/{id}` is the fleet editor.

A save of the fleet devices keeps the id of each fleet device with a saved label. A fleet run then still
finds the fleet device for Retry failed.

- `POST /api/v1/fleets/{id}/runs` — Start a fleet run that makes one build per fleet device. The body
  `{"secrets": {"<name>": "<value>"}}` holds each "must re-enter" secret of the profile once, for example
  `channel_0[psk]`. The process holds the secrets in memory for the fleet run only. Returns 202 with
  `{run_id, progress_url}`. Returns 422 before a build starts when a secret is missing, a fleet device is
  not valid, the firmware version is not installed or the fleet holds no fleet device.
- `GET /api/v1/fleet-runs/{id}` — The fleet run with `status` (`running`, `complete`, `partial` or
  `failed`) and one entry per fleet device: `label`, `variant`, `build_id`, its build status and `error`.
- `POST /api/v1/fleet-runs/{id}/retry` — Retry failed: a new build for each fleet device without a complete
  build. Takes the body of a fleet run start. Returns 202, 409 while the fleet run runs or when it has no
  failed fleet device, and 422 for a missing secret. After a restart, the body must give the secrets again.
- `GET /api/v1/fleet-runs/{id}/download` — Download all: one zip with a folder per fleet device label and
  `manifest.csv`. A folder holds the artifacts of a complete build. `manifest.csv` has one row per fleet
  device with `label`, `folder`, `variant`, `firmware_version`, `build_id`, `status`, `artifact_names` and
  `config_hash`, and no secret. Returns 409 while the fleet run runs.

The fleet run routes return 404 for an unknown fleet or fleet run. Without an admin session, a fleet run
asks for each stored secret of the profile too. The page `/fleets` lists the fleets and creates a fleet.
The page `/fleet-runs/{id}` shows the build status of each fleet device, and it updates each second.

Each operator of the instance uses each profile. The profile routes need no admin session, except to
store, load or remove the stored secrets.

Each admin POST and DELETE requires the CSRF token. A form sends it in the field `csrf_token`.
A JSON request and a DELETE request send it in the header `X-CSRF-Token`. Without a valid token,
the route returns 403.

The three config routes validate the body with `ConfigRequest`. An invalid body returns
422 with field errors, for example `channel_0.psk`. Each field error holds `loc`, `msg` and
`type` only, so a 422 never contains a submitted value.

The generate, preview, download and build routes take an optional field `firmware_version`. Without it, they use the
default firmware version. They return 422 for a field whose userPrefs key or enum value that firmware version lacks.
They also return 422 when `channels_to_write` counts more channel slots than that firmware version reads.

When the firmware version has no key scan, or no firmware version is installed, the generator leaves out each key
that v2.7.26 does not read. The generate, preview and download routes then send the header `X-Userprefs-Note`
with a message that says so, and the preview fragment shows the same message.

`/api/v1/preview` and `/api/v1/download` also take a form body with the config fields,
for example `channel_0[psk]`. When the request has the header `HX-Request: true`, the
preview returns an HTML fragment, and a 422 returns a fragment with the field errors.
Every other request gets JSON.

`/api/v1/build-firmware` takes the config by the form field `config_source`:

- `form`: the config fields, as the config form posts them.
- `current`: one field `config_json` that holds the config as JSON.
- `upload` (the default): one field `userPrefs` that holds a userPrefs file.

The optional form field `firmware_version` names an installed firmware version, for example
`v2.8.2.d5f1619`. Without it, the build uses the newest installed stable firmware version.
A firmware version that is not installed gives 422 with a field error for `firmware_version`.
A `variant` that is not in the registry of the firmware version gives 422 with a field error for
`variant`, for example "Variant not in v2.8.2.d5f1619". A firmware version without registry rows holds
no variant, so each build of it gives that error.

An htmx request to `/api/v1/build-firmware` or `/api/v1/builds/{id}` gets the progress
panel as an HTML fragment. The page `/?build=<id>` shows the progress panel of that build.
