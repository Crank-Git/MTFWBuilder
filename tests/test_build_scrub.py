"""Tests for the scrub and the secret mask of a build (feature 06, issue #20)."""

import asyncio
import base64
import json
import re
import time

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.services.cleanup_service import scrub_firmware_source
from mtfwbuilder.services.jsonc_generator import generate_jsonc
from mtfwbuilder.services.secret_mask import secret_masker

PSK_HEX = "38" + "4b" * 15 + "bc" * 16
PSK_BYTES = bytes.fromhex(PSK_HEX)
WIFI_PSK = "wifi-secret-1234"
MQTT_PASSWORD = 'mqtt"secret'
ENDED = {"complete", "failed", "cancelled"}


def _config(**extra):
    config = {
        "owner_long_name": "TestNode",
        "channels_to_write": "1",
        "channel_0[name]": "Primary",
        "channel_0[psk]": PSK_HEX,
    }
    config.update(extra)
    return json.dumps(config)


@pytest.fixture
def firmware_source(settings):
    settings.firmware_dir.mkdir(parents=True)
    return settings.firmware_dir


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


async def _start(app, client, config_json=None):
    resp = await client.post(
        "/api/v1/build-firmware",
        data={"variant": "heltec-v3", "config_source": "current", "config_json": config_json or _config()},
    )
    assert resp.status_code == 202
    return resp.json(), app.state.build_queue.get(resp.json()["build_id"])


def _sse_events(body):
    events = []
    for block in re.split(r"\r?\n\r?\n", body):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def _wait_for(ctx, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while ctx.status not in statuses:
        assert time.monotonic() < deadline, f"build stayed at {ctx.status}"
        await asyncio.sleep(0.02)


async def _wait_for_task(ctx):
    # The cancel route sets `cancelled` before the build task stops, so the test waits for the task.
    while ctx.task is None:
        await asyncio.sleep(0.02)
    await asyncio.wait({ctx.task}, timeout=10)


def _assert_scrubbed(firmware_dir):
    assert not (firmware_dir / "userPrefs.jsonc").exists()
    assert not (firmware_dir / ".pio" / "build" / "heltec-v3").exists()


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_cancel_during_compiling_gives_cancelled_and_scrubs(app, client, firmware_source, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "5")
    body, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})
    assert (firmware_source / "userPrefs.jsonc").exists()

    await client.post(f"/api/v1/builds/{body['build_id']}/cancel")
    await _wait_for_task(ctx)

    assert ctx.status == "cancelled"
    _assert_scrubbed(firmware_source)


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_cancel_scrubs_before_the_cancelled_end_event(app, client, firmware_source, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "5")
    body, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})
    userprefs_at_change = []
    record = ctx.on_change

    def watch(changed):
        userprefs_at_change.append((changed.status, (firmware_source / "userPrefs.jsonc").exists()))
        record(changed)

    ctx.on_change = watch

    resp = await client.post(f"/api/v1/builds/{body['build_id']}/cancel")
    await _wait_for_task(ctx)
    await _wait_for(ctx, ENDED)

    assert resp.json()["status"] == "cancelled"
    assert userprefs_at_change == [("cancelled", False)]


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_failed_build_scrubs_userprefs_file_and_env_build_directory(app, client, firmware_source, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_FAIL", "1")
    (firmware_source / ".pio" / "build" / "heltec-v3").mkdir(parents=True)
    _, ctx = await _start(app, client)

    await _wait_for(ctx, ENDED)

    assert ctx.status == "failed"
    _assert_scrubbed(firmware_source)


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_complete_build_scrubs_and_keeps_the_artifact(app, client, firmware_source):
    _, ctx = await _start(app, client)

    await _wait_for(ctx, ENDED)

    assert ctx.status == "complete", ctx.error
    _assert_scrubbed(firmware_source)
    download = await client.get(ctx.download_url)
    assert download.content == b"fake firmware-heltec-v3-2.7.26.54e0d8d.factory.bin"


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_timed_out_build_scrubs(app, client, firmware_source, monkeypatch):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "5")
    monkeypatch.setattr(app.state.settings, "build_timeout_seconds", 0.5)
    _, ctx = await _start(app, client)

    await _wait_for(ctx, ENDED)

    assert ctx.status == "failed"
    assert ctx.error.startswith("Build timed out")
    _assert_scrubbed(firmware_source)


@pytest.mark.usefixtures("fake_pio", "workers")
async def test_build_writes_the_userprefs_file_only_to_the_firmware_source_root(
    app, client, firmware_source, monkeypatch
):
    monkeypatch.setenv("FAKE_PIO_SLEEP", "5")
    body, ctx = await _start(app, client)
    await _wait_for(ctx, {"compiling"})

    written = sorted(p.relative_to(firmware_source) for p in firmware_source.rglob("userPrefs.jsonc"))

    assert [str(p) for p in written] == ["userPrefs.jsonc"]
    await client.post(f"/api/v1/builds/{body['build_id']}/cancel")
    await _wait_for_task(ctx)


@pytest.mark.usefixtures("fake_pio", "firmware_source")
async def test_no_log_line_on_the_progress_stream_holds_a_secret(app, client):
    config_json = _config(
        network_enabled="true",
        wifi_enabled="true",
        wifi_psk=WIFI_PSK,
        mqtt_enabled="true",
        mqtt_password=MQTT_PASSWORD,
    )
    body, ctx = await _start(app, client, config_json)
    # A stream that connects after the build ends gets no log lines, so it connects first.
    collector = asyncio.create_task(client.get(body["progress_url"]))
    await asyncio.sleep(0.1)
    app.state.build_queue.start()

    stream = await asyncio.wait_for(collector, 10)
    await app.state.build_queue.stop()

    assert ctx.status == "complete", ctx.error
    lines = [data["line"] for kind, data in _sse_events(stream.text) if kind == "log"]
    assert "-DUSERPREFS_CHANNEL_0_PSK=***" in lines
    secrets = [PSK_HEX, PSK_HEX.upper(), base64.b64encode(PSK_BYTES).decode(), "0x38", WIFI_PSK, "mqtt"]
    for secret in secrets:
        assert [line for line in lines if secret in line] == [], secret
        assert [line for line in ctx.build_log if secret in line] == [], secret


class TestSecretMasker:
    """Tests for the secret mask that a userPrefs file gives."""

    def test_masks_the_byte_array_hex_and_base64_forms_of_a_psk(self):
        mask = secret_masker(generate_jsonc(json.loads(_config())))
        byte_array = "{ " + ", ".join(f"0x{b:02x}" for b in PSK_BYTES) + " }"

        for form in [byte_array, PSK_HEX, PSK_HEX.upper(), base64.b64encode(PSK_BYTES).decode()]:
            assert mask(f"key {form} end") == "key *** end"

    def test_masks_the_escaped_form_of_a_string_value_in_a_flag(self):
        mask = secret_masker('{ "USERPREFS_MQTT_PASSWORD": "a\\"b-secret" }')

        # PlatformIO `StringifyMacro` writes `"` in a value as `\\\"`.
        assert mask('-DUSERPREFS_MQTT_PASSWORD=\\"a\\\\\\"b-secret\\"') == '-DUSERPREFS_MQTT_PASSWORD=\\"***\\"'
        assert mask('a"b-secret') == "***"

    def test_masks_each_secret_key_name_of_the_generator_and_of_upstream(self):
        keys = [
            "USERPREFS_CHANNEL_1_PSK",
            "USERPREFS_CONFIG_WIFI_PSK",
            "USERPREFS_NETWORK_WIFI_PSK",
            "USERPREFS_CONFIG_MQTT_PASSWORD",
            "USERPREFS_MQTT_PASSWORD",
            "USERPREFS_ADMIN_KEY_0",
            "USERPREFS_USE_ADMIN_KEY_2",
            "USERPREFS_CONFIG_OEM_IMAGE_DATA",
            "USERPREFS_OEM_IMAGE_DATA",
        ]
        mask = secret_masker(json.dumps({key: f"value-of-{key}" for key in keys}))

        for key in keys:
            assert mask(f"-D{key}=value-of-{key}") == f"-D{key}=***"

    def test_keeps_a_value_that_is_not_a_secret(self):
        mask = secret_masker('{ "USERPREFS_CONFIG_DEVICE_NAME": "TestNode", "USERPREFS_CHANNEL_0_NAME": "Primary" }')

        assert mask("-DUSERPREFS_CONFIG_DEVICE_NAME=TestNode") == "-DUSERPREFS_CONFIG_DEVICE_NAME=TestNode"

    def test_keeps_short_derived_forms_that_match_ordinary_log_text(self):
        # The hex form of a one-byte PSK is two characters, so a mask of it breaks every log line.
        mask = secret_masker('{ "USERPREFS_CHANNEL_0_PSK": "{ 0x01 }" }')

        assert mask("Compiling 01.cpp") == "Compiling 01.cpp"
        assert mask("-DUSERPREFS_CHANNEL_0_PSK={ 0x01 }") == "-DUSERPREFS_CHANNEL_0_PSK=***"

    def test_masks_a_byte_array_with_any_whitespace_and_hex_prefix_case(self):
        spaced = "{ " + ", ".join(f"0x{b:02x}" for b in PSK_BYTES) + " }"
        mask = secret_masker(json.dumps({"USERPREFS_CHANNEL_0_PSK": spaced}))
        tight = "{" + ",".join(f"0X{b:02X}" for b in PSK_BYTES) + "}"
        wide = "{  " + " ,\t".join(f"0x{b:x}" for b in PSK_BYTES) + "\n}"

        for form in [spaced, tight, wide]:
            assert mask(f"key {form} end") == "key *** end", form

    def test_masks_a_userprefs_byte_array_with_an_upper_case_hex_prefix(self):
        mask = secret_masker(
            json.dumps({"USERPREFS_CHANNEL_0_PSK": "{" + ",".join(f"0X{b:02X}" for b in PSK_BYTES) + "}"})
        )

        assert mask(f"key {PSK_HEX} end") == "key *** end"

    def test_userprefs_file_with_no_secret_gives_no_change(self):
        assert secret_masker("not json at all")("a line") == "a line"


class TestScrubFirmwareSource:
    def test_deletes_the_userprefs_file_and_the_env_build_directory(self, tmp_path):
        (tmp_path / "userPrefs.jsonc").write_text("{}")
        env_build = tmp_path / ".pio" / "build" / "tbeam"
        (env_build / "src").mkdir(parents=True)
        (env_build / "firmware-tbeam-2.7.26.54e0d8d.bin").write_bytes(b"\x00")
        other_env = tmp_path / ".pio" / "build" / "rak4631"
        other_env.mkdir()
        cache = tmp_path / ".pio" / "build_cache"
        cache.mkdir()

        scrub_firmware_source(tmp_path, "tbeam")

        assert not (tmp_path / "userPrefs.jsonc").exists()
        assert not env_build.exists()
        assert other_env.exists()
        assert not cache.exists()

    def test_scrub_of_a_clean_firmware_source_does_nothing(self, tmp_path):
        scrub_firmware_source(tmp_path, "tbeam")
