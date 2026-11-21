"""Tests for the UF2 steps and the download-only note of a complete build (FR-browser-flashing-10)."""

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import Artifact
from tests.test_flash_panel import _complete_panel, render_complete_panel

CONNECT_STEP = "Connect the device with USB."
RESET_STEP = "Press the reset button two times quickly. A USB drive appears."
BOOTSEL_STEP = "Hold the BOOTSEL button while you connect the device with USB. A USB drive appears."
COPY_STEP = "Copy the .uf2 file to the drive. The device restarts when the copy ends."
DOWNLOAD_ONLY = "The browser cannot flash this device. Flash the artifact with the tool of the device vendor."


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def workers(app, firmware_source, fake_pio):
    app.state.build_queue.start()
    yield app.state.build_queue
    await app.state.build_queue.stop()


def _steps(panel):
    """Return the text of each item of the UF2 step list, in order."""
    start = panel.elements.index(("ol", panel.by_id("uf2-steps")))
    items = [
        attrs["id"]
        for tag, attrs in panel.elements[start:]
        if tag == "li" and attrs.get("id", "").startswith("uf2-step-")
    ]
    return [panel.text_of(item) for item in items]


@pytest.mark.usefixtures("workers")
async def test_nrf52_build_shows_the_uf2_steps_and_no_flash_control(app, client):
    ctx, panel = await _complete_panel(app, client, "rak4631")

    assert ctx.variant.architecture == "nrf52840"
    assert panel.by_id("uf2-panel") is not None
    assert panel.by_id("flash-start") is None
    assert panel.by_id("flash-panel") is None
    steps = _steps(panel)
    assert steps[1:] == [CONNECT_STEP, RESET_STEP, COPY_STEP]
    assert steps[0].startswith("Download the UF2 file:")


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", ["rak11310", "pico"])
async def test_rp2040_builds_show_the_bootsel_steps_and_no_flash_control(app, client, variant):
    ctx, panel = await _complete_panel(app, client, variant)

    assert ctx.variant.architecture == "rp2040"
    assert panel.by_id("flash-start") is None
    assert _steps(panel)[1:] == [BOOTSEL_STEP, COPY_STEP]


# The firmware fixture has no rp2350 variant, so the test renders the template.
def test_rp2350_build_shows_the_bootsel_steps_and_no_flash_control(app):
    uf2 = Artifact(name="firmware-pico2-2.7.26.uf2", size=1, kind="uf2")
    panel = render_complete_panel(app, "rp2350", [uf2])

    assert panel.by_id("flash-start") is None
    assert panel.by_id("uf2-download")["href"] == f"/api/v1/builds/b1/artifacts/{uf2.name}"
    assert _steps(panel)[1:] == [BOOTSEL_STEP, COPY_STEP]


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", ["rak4631", "rak11310", "pico"])
async def test_uf2_download_control_links_the_uf2_artifact_on_the_artifact_route(app, client, variant):
    ctx, panel = await _complete_panel(app, client, variant)

    (uf2,) = [a for a in ctx.artifacts if a.kind == "uf2"]
    link = panel.by_id("uf2-download")
    assert link["href"] == f"/api/v1/builds/{ctx.build_id}/artifacts/{uf2.name}"
    assert "download" in link
    assert panel.text_of("uf2-download") == uf2.name


# The firmware fixture has no stm32 variant, so the test renders the template.
def test_stm32_build_shows_the_download_only_note_and_no_uf2_steps(app):
    panel = render_complete_panel(app, "stm32", [Artifact(name="firmware-rak3172-2.7.26.bin", size=1, kind="app")])

    assert panel.text_of("download-only") == DOWNLOAD_ONLY
    assert panel.by_id("uf2-panel") is None
    assert panel.by_id("flash-start") is None


@pytest.mark.usefixtures("workers")
async def test_esp32_build_shows_no_uf2_steps_and_no_download_only_note(app, client):
    _, panel = await _complete_panel(app, client, "heltec-v3")

    assert panel.by_id("flash-panel") is not None
    assert panel.by_id("uf2-panel") is None
    assert panel.by_id("download-only") is None


# The firmware fixture has no nrf54l15 variant, so the test renders the template.
def test_nrf54l15_build_shows_the_download_only_note(app):
    artifact = Artifact(name="firmware-xiao_nrf54l15-2.7.26.zip", size=1, kind="ota-zip")
    panel = render_complete_panel(app, "nrf54l15", [artifact])

    assert panel.text_of("download-only") == DOWNLOAD_ONLY
    assert panel.by_id("uf2-panel") is None


def test_uf2_panel_without_a_uf2_artifact_says_that_the_build_has_no_uf2_file(app):
    artifact = Artifact(name="firmware-pico2-2.7.26.hex", size=1, kind="hex")
    panel = render_complete_panel(app, "rp2350", [artifact])

    assert panel.text_of("uf2-step-1") == "This build has no UF2 file."
    assert panel.by_id("uf2-download") is None
