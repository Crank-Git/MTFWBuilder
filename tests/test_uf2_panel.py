"""Tests for the UF2 steps and the download-only note of a complete build (FR-browser-flashing-10)."""

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import Artifact
from mtfwbuilder.services.device_registry import DeviceVariant
from tests.test_flash_panel import _complete_panel, _Panel

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
async def workers(app, settings, fake_pio):
    settings.firmware_dir.mkdir(parents=True)
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

    assert ctx.variant.pio_platform == "nrf52840"
    assert panel.by_id("uf2-panel") is not None
    assert panel.by_id("flash-start") is None
    assert panel.by_id("flash-panel") is None
    steps = _steps(panel)
    assert steps[1:] == [CONNECT_STEP, RESET_STEP, COPY_STEP]
    assert steps[0].startswith("Download the UF2 file:")


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", ["rak11310", "pico2"])
async def test_rp2040_and_rp2350_builds_show_the_bootsel_steps_and_no_flash_control(app, client, variant):
    _, panel = await _complete_panel(app, client, variant)

    assert panel.by_id("flash-start") is None
    assert _steps(panel)[1:] == [BOOTSEL_STEP, COPY_STEP]


@pytest.mark.usefixtures("workers")
@pytest.mark.parametrize("variant", ["rak4631", "rak11310", "pico2"])
async def test_uf2_download_control_links_the_uf2_artifact_on_the_artifact_route(app, client, variant):
    ctx, panel = await _complete_panel(app, client, variant)

    (uf2,) = [a for a in ctx.artifacts if a.kind == "uf2"]
    link = panel.by_id("uf2-download")
    assert link["href"] == f"/api/v1/builds/{ctx.build_id}/artifacts/{uf2.name}"
    assert "download" in link
    assert panel.text_of("uf2-download") == uf2.name


@pytest.mark.usefixtures("workers")
async def test_stm32_build_shows_the_download_only_note_and_no_uf2_steps(app, client):
    ctx, panel = await _complete_panel(app, client, "rak3172")

    assert ctx.variant.pio_platform == "stm32"
    assert panel.text_of("download-only") == DOWNLOAD_ONLY
    assert panel.by_id("uf2-panel") is None
    assert panel.by_id("flash-start") is None


@pytest.mark.usefixtures("workers")
async def test_esp32_build_shows_no_uf2_steps_and_no_download_only_note(app, client):
    _, panel = await _complete_panel(app, client, "heltec-v3")

    assert panel.by_id("flash-panel") is not None
    assert panel.by_id("uf2-panel") is None
    assert panel.by_id("download-only") is None


# The registry has no nrf54l15 variant yet, so the test renders the partial directly.
def test_nrf54l15_build_shows_the_download_only_note(app):
    class Build:
        build_id = "b1"
        variant = DeviceVariant(
            id="xiao_nrf54l15", name="XIAO", manufacturer="Seeed", architecture="nrf52", pio_platform="nrf54l15"
        )

    artifact = Artifact(name="firmware-xiao_nrf54l15-2.7.26.zip", size=1, kind="ota-zip")
    template = app.state.templates.get_template("partials/uf2_panel.html")
    panel = _Panel()
    panel.feed(template.render(build=Build, artifacts=[(artifact, "/x")]))

    assert panel.text_of("download-only") == DOWNLOAD_ONLY
    assert panel.by_id("uf2-panel") is None


def test_uf2_panel_without_a_uf2_artifact_says_that_the_build_has_no_uf2_file(app):
    class Build:
        build_id = "b1"
        variant = DeviceVariant(
            id="pico2", name="Pico 2", manufacturer="Raspberry Pi", architecture="rp2040", pio_platform="rp2350"
        )

    artifact = Artifact(name="firmware-pico2-2.7.26.hex", size=1, kind="hex")
    template = app.state.templates.get_template("partials/uf2_panel.html")
    panel = _Panel()
    panel.feed(template.render(build=Build, artifacts=[(artifact, "/x")]))

    assert panel.text_of("uf2-step-1") == "This build has no UF2 file."
    assert panel.by_id("uf2-download") is None
