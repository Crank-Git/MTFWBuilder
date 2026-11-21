"""Tests for the firmware catalog: the upstream stable and alpha firmware versions (feature 09, FR 1 and 2)."""

import json
from pathlib import Path

import pytest
import requests
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.auth import SESSION_COOKIE, create_session_token
from mtfwbuilder.main import lifespan
from mtfwbuilder.services import firmware_catalog
from mtfwbuilder.services.firmware_catalog import MESHTASTIC_LIST_URL, CatalogUnavailable, FirmwareCatalog
from mtfwbuilder.services.firmware_updater import GITHUB_REPO_API, ReleaseNotFound

FIXTURES = Path(__file__).parent / "fixtures" / "firmware"
# A recorded response of api.meshtastic.org, and of the GitHub releases API (see `SOURCE.md`).
MESHTASTIC_LIST = json.loads((FIXTURES / "firmware_list.json").read_text())
RELEASES = json.loads((FIXTURES / "releases.json").read_text())
GITHUB_RELEASES_URL = f"{GITHUB_REPO_API}/releases"


class _Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


@pytest.fixture
def upstream(monkeypatch):
    """Answer each upstream request from the recorded responses, and return the request list.

    A test sets `upstream.meshtastic` or `upstream.github` to a `_Response` or to an exception that the request raises.
    """

    class Calls(list):
        meshtastic = _Response(MESHTASTIC_LIST)
        github = _Response(RELEASES)

    calls = Calls()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        answer = {MESHTASTIC_LIST_URL: calls.meshtastic, GITHUB_RELEASES_URL: calls.github}[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(firmware_catalog.requests, "get", fake_get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return calls


def _tags(entries):
    return [entry["tag"] for entry in entries]


def test_list_from_meshtastic_gives_the_stable_and_alpha_tags_newest_first(upstream):
    available = FirmwareCatalog().available()

    assert available["source"] == "api.meshtastic.org"
    assert _tags(available["stable"]) == ["v2.7.26.54e0d8d", "v2.7.15.567b8ea"]
    assert _tags(available["alpha"]) == [entry["id"] for entry in MESHTASTIC_LIST["releases"]["alpha"]]
    assert available["stable"][0] == {
        "tag": "v2.7.26.54e0d8d",
        "title": "Meshtastic Firmware 2.7.26.54e0d8d Beta",
        "date": None,
        "revoked": False,
    }
    assert [url for url, _ in upstream] == [MESHTASTIC_LIST_URL]


def test_list_from_meshtastic_drops_an_id_that_is_not_a_firmware_version(upstream):
    data = json.loads(json.dumps(MESHTASTIC_LIST))
    data["releases"]["alpha"].insert(0, {"id": "v2.8.3", "title": "No hash"})
    data["releases"]["alpha"].insert(0, {"id": "../etc", "title": "Not a tag"})
    upstream.meshtastic = _Response(data)

    available = FirmwareCatalog().available()

    assert "v2.8.3" not in _tags(available["alpha"])
    assert "../etc" not in _tags(available["alpha"])


def test_meshtastic_request_sends_the_user_agent_and_a_short_timeout(upstream):
    FirmwareCatalog().available()

    [(_url, kwargs)] = upstream
    assert kwargs["headers"]["User-Agent"] == "MTFWBuilder"
    assert kwargs["timeout"] <= 10


def test_with_meshtastic_unreachable_the_list_comes_from_the_github_releases_api(upstream):
    upstream.meshtastic = requests.ConnectionError("api.meshtastic.org is unreachable")

    available = FirmwareCatalog().available()

    assert available["source"] == "github"
    assert _tags(available["stable"]) == ["v2.7.26.54e0d8d"]
    assert _tags(available["alpha"]) == [
        "v2.8.2.d5f1619",
        "v2.8.2.4c89b69",
        "v2.8.1.8e6a88d",
        "v2.8.0.47db0e3",
        "v2.8.0.7239fe8",
        "v2.7.25.104df5f",
        "v2.7.24.472b14c",
        "v2.7.23.b246bcd",
        "v2.7.22.96dd647",
    ]
    assert available["stable"][0] == {
        "tag": "v2.7.26.54e0d8d",
        "title": "Meshtastic Firmware 2.7.26.54e0d8d Beta",
        "date": "2026-06-24T11:19:27Z",
        "revoked": False,
    }
    assert [url for url, _ in upstream] == [MESHTASTIC_LIST_URL, GITHUB_RELEASES_URL]


def test_github_list_marks_a_revoked_alpha(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")

    alpha = {entry["tag"]: entry for entry in FirmwareCatalog().available()["alpha"]}

    assert alpha["v2.8.0.47db0e3"]["revoked"] is True
    assert alpha["v2.8.2.4c89b69"]["revoked"] is True
    assert alpha["v2.8.2.d5f1619"]["revoked"] is False


def test_github_list_omits_a_draft(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    releases = json.loads(json.dumps(RELEASES))
    releases[0]["draft"] = True
    upstream.github = _Response(releases)

    assert "v2.8.2.d5f1619" not in _tags(FirmwareCatalog().available()["alpha"])


@pytest.mark.parametrize(
    "answer",
    [
        _Response({"error": "down"}, status_code=503),
        _Response(ValueError("not JSON")),
        _Response({"pullRequests": []}),
        _Response({"releases": {"stable": "not a list", "alpha": []}}),
    ],
    ids=["http-error", "not-json", "no-releases", "wrong-type"],
)
def test_a_failed_or_malformed_meshtastic_answer_falls_back_to_github(upstream, answer):
    upstream.meshtastic = answer

    assert FirmwareCatalog().available()["source"] == "github"


def test_github_request_sends_the_user_agent_and_the_token_when_set(upstream, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    upstream.meshtastic = requests.ConnectionError("unreachable")

    FirmwareCatalog().available()

    _url, kwargs = upstream[1]
    assert kwargs["headers"]["User-Agent"] == "MTFWBuilder"
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["timeout"] <= 10


def test_github_request_sends_no_authorization_without_a_token(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")

    FirmwareCatalog().available()

    _url, kwargs = upstream[1]
    assert "Authorization" not in kwargs["headers"]


def test_both_upstreams_failed_raises_catalog_unavailable(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    upstream.github = requests.Timeout("timed out")

    with pytest.raises(CatalogUnavailable):
        FirmwareCatalog().available()


def test_list_is_cached_for_the_cache_time(upstream):
    now = [1000.0]
    catalog = FirmwareCatalog(cache_seconds=300, clock=lambda: now[0])

    catalog.available()
    now[0] += 299
    catalog.available()
    assert len(upstream) == 1

    now[0] += 2
    catalog.available()
    assert len(upstream) == 2


def test_a_failure_is_not_cached(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    upstream.github = requests.ConnectionError("unreachable")
    catalog = FirmwareCatalog()
    with pytest.raises(CatalogUnavailable):
        catalog.available()

    upstream.meshtastic = _Response(MESHTASTIC_LIST)

    assert catalog.available()["source"] == "api.meshtastic.org"


@pytest.mark.parametrize(("channel", "tag"), [("stable", "v2.7.26.54e0d8d"), ("alpha", "v2.8.2.d5f1619")])
def test_resolve_gives_the_newest_tag_of_the_channel(upstream, channel, tag):
    assert FirmwareCatalog().resolve(channel) == tag


def test_resolve_skips_a_revoked_alpha(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    upstream.github = _Response([r for r in RELEASES if r["tag_name"] != "v2.8.2.d5f1619"])

    # `v2.8.2.4c89b69` is the newest alpha now, and its release name says it is revoked.
    assert FirmwareCatalog().resolve("alpha") == "v2.8.1.8e6a88d"


def test_resolve_raises_release_not_found_for_an_empty_channel(upstream):
    upstream.meshtastic = _Response({"releases": {"stable": [], "alpha": []}, "pullRequests": []})

    with pytest.raises(ReleaseNotFound):
        FirmwareCatalog().resolve("stable")


def test_resolve_raises_release_not_found_when_both_upstreams_failed(upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    upstream.github = requests.ConnectionError("unreachable")

    with pytest.raises(ReleaseNotFound):
        FirmwareCatalog().resolve("alpha")


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def admin(client, settings):
    client.cookies.set(SESSION_COOKIE, create_session_token(settings))
    return client


async def test_available_route_returns_the_stable_and_alpha_lists(admin, upstream):
    resp = await admin.get("/api/v1/firmware/available")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "api.meshtastic.org"
    assert _tags(body["stable"]) == ["v2.7.26.54e0d8d", "v2.7.15.567b8ea"]
    assert body["alpha"][0] == {
        "tag": "v2.8.2.d5f1619",
        "title": "Meshtastic Firmware 2.8.2.d5f1619 Alpha",
        "date": None,
        "revoked": False,
    }


async def test_available_route_uses_github_when_meshtastic_is_unreachable(admin, upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")

    body = (await admin.get("/api/v1/firmware/available")).json()

    assert body["source"] == "github"
    assert _tags(body["stable"]) == ["v2.7.26.54e0d8d"]


async def test_available_route_returns_502_when_both_upstreams_failed(admin, upstream):
    upstream.meshtastic = requests.ConnectionError("unreachable")
    upstream.github = requests.ConnectionError("unreachable")

    resp = await admin.get("/api/v1/firmware/available")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Cannot reach api.meshtastic.org or GitHub."


async def test_available_route_requires_an_admin_session(client, upstream):
    resp = await client.get("/api/v1/firmware/available")

    assert resp.status_code == 401
    assert upstream == []


async def test_lifespan_gives_the_install_service_the_catalog_resolver(app):
    async with lifespan(app):
        assert app.state.firmware_installs.resolver == app.state.firmware_catalog.resolve
