"""Tests for the validation of the channel precision and the LoRa channel number (#157)."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_config_enums import form_options

PRECISION_MESSAGE = "Value error, the position precision is not a whole number from 0 to 32"
CHANNEL_NUM_MESSAGE = "Value error, the frequency slot is not a whole number from 0 to 4294967295"


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def errors_at(resp, *loc: str) -> list[str]:
    """Return the messages of the field errors at `loc` in a 422 response."""
    return [err["msg"] for err in resp.json()["detail"] if err["loc"][1:] == list(loc)]


# Acceptance criteria


@pytest.mark.asyncio
async def test_generate_rejects_a_precision_of_high_with_a_field_error(client):
    resp = await client.post("/api/v1/generate", json={"channel_0[precision]": "high"})
    assert resp.status_code == 422
    assert errors_at(resp, "channel_0", "precision") == [PRECISION_MESSAGE]


@pytest.mark.asyncio
async def test_generate_rejects_a_channel_number_of_x_with_a_field_error(client):
    resp = await client.post("/api/v1/generate", json={"lora_channel_num": "x"})
    assert resp.status_code == 422
    assert errors_at(resp, "lora_channel_num") == [CHANNEL_NUM_MESSAGE]


# Range limits


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "32"])
async def test_precision_that_is_empty_or_from_0_to_32_is_valid(client, value):
    resp = await client.post("/api/v1/generate", json={"channel_0[precision]": value})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_empty_precision_writes_no_precision_key(client):
    resp = await client.post("/api/v1/generate", json={"channel_0[precision]": ""})
    assert "USERPREFS_CHANNEL_0_PRECISION" not in resp.json()["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["33", "-1", "1.5", "١٤"])
async def test_precision_outside_0_to_32_returns_422(client, value):
    resp = await client.post("/api/v1/generate", json={"channel_7[precision]": value})
    assert resp.status_code == 422
    assert errors_at(resp, "channel_7", "precision") == [PRECISION_MESSAGE]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "4294967295"])
async def test_channel_number_that_is_empty_or_a_uint32_is_valid(client, value):
    resp = await client.post("/api/v1/generate", json={"lora_channel_num": value})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["4294967296", "-1", "1.5"])
async def test_channel_number_outside_the_uint32_range_returns_422(client, value):
    resp = await client.post("/api/v1/generate", json={"lora_channel_num": value})
    assert resp.status_code == 422
    assert errors_at(resp, "lora_channel_num") == [CHANNEL_NUM_MESSAGE]


# Form controls


@pytest.mark.asyncio
async def test_every_precision_option_of_the_build_page_is_valid(client):
    options, _ = await form_options(client)
    values = options["channel_0[precision]"]
    assert values
    for value in values:
        resp = await client.post("/api/v1/generate", json={"channel_0[precision]": value})
        assert resp.status_code == 200, value


@pytest.mark.asyncio
async def test_default_channel_number_of_the_build_page_is_valid(client):
    _, html = await form_options(client)
    assert 'name="lora_channel_num" value="31"' in html
    resp = await client.post("/api/v1/generate", json={"lora_channel_num": "31"})
    assert resp.status_code == 200
