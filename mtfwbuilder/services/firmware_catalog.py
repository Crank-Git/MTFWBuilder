"""Firmware catalog: the upstream stable and alpha firmware versions, for the admin page and for installs by channel.

The catalog reads `https://api.meshtastic.org/github/firmware/list`. That API has no published docs, so its shape
comes from `meshtastic/web-flasher` (`types/api.ts`, `FirmwareReleases`). When it fails, the catalog reads the
GitHub releases API (version 2022-11-28) instead.
"""

import logging
import threading
import time
from collections.abc import Callable

import requests

from mtfwbuilder.services.firmware_updater import GITHUB_REPO_API, TAG, ReleaseNotFound, github_headers

logger = logging.getLogger("mtfwbuilder.firmware_catalog")

MESHTASTIC_LIST_URL = "https://api.meshtastic.org/github/firmware/list"
# The admin page waits for the list, so a slow upstream fails fast and the fallback runs.
TIMEOUT_SECONDS = 10
CHANNELS = ("stable", "alpha")


class CatalogUnavailable(ReleaseNotFound):
    """Both api.meshtastic.org and the GitHub releases API failed."""


def _entry(tag: str, title: str | None, date: str | None) -> dict:
    # Upstream adds `(Revoked)` to the title of a revoked alpha, and api.meshtastic.org omits it from its list.
    return {"tag": tag, "title": title or tag, "date": date, "revoked": "revoked" in (title or "").lower()}


def _newest_first(entries: list[dict]) -> list[dict]:
    # Neither upstream documents an order, so the version numbers decide, then the date.
    return sorted(entries, key=lambda e: (tuple(map(int, e["tag"][1:].split(".")[:3])), e["date"] or ""), reverse=True)


def _from_meshtastic() -> dict:
    """Return the lists of api.meshtastic.org. Raises `requests.RequestException`, `ValueError` or `KeyError`."""
    response = requests.get(MESHTASTIC_LIST_URL, headers={"User-Agent": "MTFWBuilder"}, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    releases = response.json()["releases"]
    lists = {}
    for channel in CHANNELS:
        if not isinstance(releases[channel], list):
            raise ValueError(f"api.meshtastic.org gives no {channel} list")
        # The list gives no release date, so `date` stays empty.
        lists[channel] = _newest_first(
            [_entry(r["id"], r.get("title"), None) for r in releases[channel] if TAG.fullmatch(r["id"])]
        )
    return {"source": "api.meshtastic.org", **lists}


def _from_github() -> dict:
    """Return the lists of the GitHub releases API, with each prerelease as an alpha.

    Raises `requests.RequestException`, `ValueError` or `KeyError`.
    """
    url = f"{GITHUB_REPO_API}/releases"
    response = requests.get(url, params={"per_page": 100}, headers=github_headers(), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    lists = {channel: [] for channel in CHANNELS}
    for release in response.json():
        if release["draft"] or not TAG.fullmatch(release["tag_name"]):
            continue
        channel = "alpha" if release["prerelease"] else "stable"
        lists[channel].append(_entry(release["tag_name"], release["name"], release["published_at"]))
    return {"source": "github", **{channel: _newest_first(entries) for channel, entries in lists.items()}}


class FirmwareCatalog:
    """Give the upstream firmware versions, and keep the last list in memory for `cache_seconds`.

    The methods make blocking requests, so an async caller runs them in a worker thread.
    """

    def __init__(self, cache_seconds: float = 300, clock: Callable[[], float] = time.monotonic):
        self.cache_seconds = cache_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0

    def available(self) -> dict:
        """Return `{source, stable, alpha}`, each list newest first, from api.meshtastic.org or from GitHub.

        Each entry has `tag`, `title`, `date` (None from api.meshtastic.org) and `revoked`.
        Raises `CatalogUnavailable` when both upstreams fail. A failure is not cached.
        """
        # The lock keeps two threads with an expired cache to one upstream request.
        with self._lock:
            if self._cached is not None and self.clock() - self._cached_at < self.cache_seconds:
                return self._cached
            try:
                result = _from_meshtastic()
            except (requests.RequestException, ValueError, KeyError, TypeError) as e:
                logger.warning(f"api.meshtastic.org failed, so the firmware list comes from GitHub: {e}")
                try:
                    result = _from_github()
                except (requests.RequestException, ValueError, KeyError, TypeError) as e2:
                    raise CatalogUnavailable(f"Cannot reach api.meshtastic.org or GitHub: {e2}") from e2
            self._cached, self._cached_at = result, self.clock()
            return result

    def resolve(self, channel: str) -> str:
        """Return the newest tag of the channel `stable` or `alpha` that is not revoked.

        This is the resolver of `FirmwareInstalls`. Raises `ReleaseNotFound` when the channel has no such tag,
        and `CatalogUnavailable` when both upstreams fail.
        """
        for entry in self.available()[channel]:
            if not entry["revoked"]:
                return entry["tag"]
        raise ReleaseNotFound(f"Upstream lists no {channel} firmware version")
