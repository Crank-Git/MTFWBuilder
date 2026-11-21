"""Tests of the flash write flow (FR-browser-flashing-3 to -9, and the edge cases of feature browser-flashing).

flash.js runs in Node with a fake esptool-js and a fake serial port (`tests/js/flash_flow_harness.cjs`).
No test opens a real port or writes to a device.
"""

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

HARNESS = REPO_ROOT / "tests" / "js" / "flash_flow_harness.cjs"
FLASH_JS = REPO_ROOT / "static" / "js" / "flash.js"
TEMPLATE = REPO_ROOT / "templates" / "partials" / "flash_panel.html"
NAME = "firmware-heltec-v4-2.7.26.54e0d8d"
ERASE_WARNING = "This erases the device. Saved settings, messages and keys are lost."
CONNECTING = "Connecting. If this takes more than 10 seconds, hold BOOT and press RESET."
DONE_FULL = "Done. The device restarts with your factory defaults."
NO_ESP32 = "No ESP32 found on this port."
OTHER_TABLE = "The partition table on the device differs from this build. Use Full install."
LOAD_FAILED = "The flashing tool did not load. Use the download links."
# The harness logs each loader call that changes the flash or the port.
DEVICE_CALLS = ("transport:new", "loader:eraseFlash", "loader:writeFlash")


def run(**scenario):
    """Return the event log and the final panel state after the harness runs `scenario`."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the flash.js tests need it")
    command = [node, "--experimental-vm-modules", "--no-warnings", str(HARNESS), str(FLASH_JS), json.dumps(scenario)]
    out = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def first(log, prefix):
    return next(i for i, event in enumerate(log) if event.startswith(prefix))


def device_calls(log):
    return [event for event in log if event.startswith(DEVICE_CALLS)]


# FR-browser-flashing-7 (acceptance criterion: full install shows the erase warning before any write)


def test_full_install_shows_the_erase_warning_before_it_opens_the_port():
    result = run(mode="full")

    assert "show:flash-confirm" in result["log"]
    assert device_calls(result["log"]) == []
    assert result["writes"] == []
    assert "flash-confirm" in result["shown"]


def test_full_install_writes_only_after_the_operator_confirms_the_erase_warning():
    log = run(mode="full", confirm=True)["log"]

    warning = first(log, "show:flash-confirm")
    confirm = log.index("operator:confirm")
    assert warning < confirm < first(log, "transport:new")
    assert confirm < first(log, "loader:eraseFlash") < first(log, "loader:writeFlash")


def test_full_install_cancel_at_the_erase_warning_writes_nothing():
    result = run(mode="full", confirm=False)

    assert device_calls(result["log"]) == []
    assert "flash-ready" in result["shown"]
    assert "flash-confirm" not in result["shown"]


def test_panel_holds_the_erase_warning_text():
    assert ERASE_WARNING in TEMPLATE.read_text()


def test_full_install_retry_shows_the_erase_warning_again_before_it_writes():
    log = run(mode="full", confirm=True, retry=True, writeError="The device has been lost.")["log"]

    retry = log.index("operator:retry")
    second_warning = log.index("show:flash-confirm", retry)
    second_confirm = log.index("operator:confirm", retry)
    second_write = log.index("loader:writeFlash:0x0:1001", retry)
    assert retry < second_warning < second_confirm < second_write


# FR-browser-flashing-3


def test_flash_opens_the_web_serial_port_picker():
    log = run(mode="update")["log"]

    assert log.index("operator:flash") < log.index("serial:requestPort")


def test_cancelled_port_picker_returns_to_the_ready_state_and_touches_no_device():
    result = run(mode="full", portCancel=True)

    assert device_calls(result["log"]) == []
    assert "show:flash-confirm" not in result["log"]
    assert "flash-ready" in result["shown"]
    assert "flash-error" not in result["shown"]


# FR-browser-flashing-5


def test_full_install_erases_then_writes_the_factory_artifact_at_0x0():
    result = run(mode="full", confirm=True)

    writes = [event for event in result["log"] if event.startswith("loader:writeFlash")]
    assert writes == ["loader:writeFlash:0x0:1001"]
    assert first(result["log"], "loader:eraseFlash") < first(result["log"], "loader:writeFlash")
    assert f"fetch:/api/v1/builds/b1/artifacts/{NAME}.factory.bin" in result["log"]
    assert result["writes"][0]["eraseAll"] is False


def test_write_keeps_the_flash_parameters_of_the_artifact():
    write = run(mode="full", confirm=True)["writes"][0]

    assert (write["flashMode"], write["flashFreq"], write["flashSize"]) == ("keep", "keep", "keep")
    assert write["compress"] is True


# FR-browser-flashing-6


def test_update_writes_the_app_artifact_at_the_app_offset_of_the_manifest():
    result = run(mode="update")

    assert "show:flash-confirm" not in result["log"]
    assert "loader:eraseFlash" not in result["log"]
    assert [event for event in result["log"] if event.startswith("loader:writeFlash")] == [
        "loader:writeFlash:0x10000:601"
    ]
    assert f"fetch:/api/v1/builds/b1/artifacts/{NAME}.mt.json" in result["log"]
    assert result["writes"][0]["eraseAll"] is False


def test_update_reads_the_app_offset_from_a_manifest_with_another_layout():
    table = [
        {"name": "nvs", "type": "data", "subtype": "nvs", "offset": "0x9000", "size": "20K"},
        {"name": "app0", "type": "app", "subtype": "ota_0", "offset": 0x20000, "size": "3M"},
    ]

    result = run(mode="update", manifest={"part": table}, deviceTable=[[0x9000, 0x5000], [0x20000, 0x300000]])

    assert "loader:writeFlash:0x20000:601" in result["log"]
    assert "flash-done" in result["shown"]


def test_update_without_an_app_partition_in_the_manifest_writes_nothing():
    result = run(mode="update", manifest={"version": "2.7.26.54e0d8d"})

    assert "loader:writeFlash" not in " ".join(result["log"])
    assert "flash-error" in result["shown"]
    assert "Full install" in result["text"]["flash-error-text"]


# Edge case: update on a device with another partition table


def test_update_on_a_device_with_another_partition_table_advises_full_install_and_writes_nothing():
    result = run(mode="update", deviceTable=[[0x9000, 0x5000], [0xE000, 0x2000], [0x10000, 0x300000]])

    assert "loader:readFlash:0x8000:0xc00" in result["log"]
    assert "loader:writeFlash" not in " ".join(result["log"])
    assert result["text"]["flash-error-text"] == OTHER_TABLE
    assert "flash-error" in result["shown"]


def test_update_checks_the_partition_table_before_it_writes():
    log = run(mode="update")["log"]

    assert first(log, "loader:readFlash") < first(log, "loader:writeFlash")


# FR-browser-flashing-8


def test_progress_shows_each_step_in_order_with_a_percent():
    result = run(mode="full", confirm=True)
    steps = [event[len("step:") :] for event in result["log"] if event.startswith("step:")]

    assert [s for i, s in enumerate(steps) if i == 0 or steps[i - 1] != s] == [
        CONNECTING,
        "Erasing.",
        "Writing.",
        "Verifying.",
        "Resetting.",
    ]
    bars = [event[len("bar:") :] for event in result["log"] if event.startswith("bar:")]
    assert "50%" in bars
    assert bars[-1] == "100%"
    assert result["barWidth"] == "100%"


def test_write_verifies_the_flash_with_the_md5_of_the_image():
    write = run(mode="full", confirm=True)["writes"][0]

    assert write["md5"] == write["expectedMd5"]
    assert write["sameBytes"] is True


def test_update_progress_skips_the_erase_step():
    log = run(mode="update")["log"]

    assert "step:Erasing." not in log
    assert "step:Writing." in log


# FR-browser-flashing-9


def test_flash_hard_resets_the_device_and_shows_done():
    result = run(mode="full", confirm=True)

    log = result["log"]
    assert first(log, "loader:writeFlash") < log.index("loader:after:hard_reset") < log.index("transport:disconnect")
    assert result["text"]["flash-done"] == DONE_FULL
    assert "flash-done" in result["shown"]
    assert "flash-error" not in result["shown"]


def test_update_done_says_that_the_device_keeps_its_saved_settings():
    result = run(mode="update")

    assert result["text"]["flash-done"] == "Done. The device restarts and keeps its saved settings."


# Behaviour rule: baud rate 921600, fallback to 115200 on a connect error


def test_connect_uses_921600_baud():
    log = run(mode="update")["log"]

    assert [event for event in log if event.startswith("loader:new")] == ["loader:new:921600:true"]


def test_connect_error_at_921600_retries_at_115200_on_a_new_transport():
    result = run(mode="update", failAt=[921600])

    log = result["log"]
    assert [event for event in log if event.startswith("loader:new")] == [
        "loader:new:921600:true",
        "loader:new:115200:true",
    ]
    assert log.index("loader:main:921600") < log.index("transport:disconnect") < log.index("loader:main:115200")
    assert "flash-done" in result["shown"]


# Edge case: a port that is not an ESP32


def test_sync_failure_shows_no_esp32_found_and_writes_nothing():
    result = run(mode="update", syncFail=True)

    assert result["text"]["flash-error-text"] == NO_ESP32
    assert "loader:writeFlash" not in " ".join(result["log"])
    assert "flash-retry" in result["shown"]


# Edge case: the operator unplugs the device during a write. Spec flow step 8: error, Retry, esptool.py command.


def test_write_error_shows_the_error_retry_and_the_full_install_command():
    result = run(mode="full", confirm=True, writeError="The device has been lost.")

    assert "flash-error" in result["shown"]
    assert result["text"]["flash-error-text"] == "The device has been lost."
    assert result["text"]["flash-command"] == (
        f"esptool.py --port <PORT> --baud 921600 write_flash --erase-all 0x0 {NAME}.factory.bin"
    )
    assert "flash-retry" in result["shown"]
    assert "transport:disconnect" in result["log"]


def test_update_error_shows_the_update_command_with_the_manifest_offset():
    result = run(mode="update", writeError="The device has been lost.")

    assert result["text"]["flash-command"] == f"esptool.py --port <PORT> --baud 921600 write_flash 0x10000 {NAME}.bin"


def test_retry_after_a_write_error_flashes_the_same_port_again():
    result = run(mode="update", retry=True, writeError="The device has been lost.")

    log = result["log"]
    assert log.count("serial:requestPort") == 1
    assert log.count("loader:writeFlash:0x10000:601") == 2
    assert "flash-done" in result["shown"]


def test_artifact_download_error_shows_the_error_and_touches_no_device():
    result = run(mode="update", missing=[f"{NAME}.bin"])

    assert device_calls(result["log"]) == []
    assert "flash-error" in result["shown"]
    assert f"{NAME}.bin" in result["text"]["flash-error-text"]


# The esptool-js load failure (spec: Screens & states, unsupported)


def test_esptool_load_failure_shows_a_message_and_the_downloads():
    result = run(mode="full", loadFails=True)

    assert sorted(result["shown"]) == ["flash-downloads", "flash-load-failed"]
    assert result["startDisabled"] is True
    assert LOAD_FAILED in TEMPLATE.read_text()
