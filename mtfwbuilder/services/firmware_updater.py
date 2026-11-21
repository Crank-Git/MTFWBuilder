"""Firmware versions: the install and the removal of firmware sources, and the import of the old layout.

Each firmware source is the directory `<firmware_dir>/<tag>/`, and the `firmware_sources` table holds its record.
"""

import asyncio
import logging
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests

from mtfwbuilder.config import Settings
from mtfwbuilder.database import delete_firmware_source, find_firmware_source, record_firmware_source
from mtfwbuilder.models import InstallStatus
from mtfwbuilder.services.build_service import BuildQueue

logger = logging.getLogger("mtfwbuilder.firmware_updater")

# The tag is also a directory name, so this pattern keeps a path separator out of it.
TAG = re.compile(r"v\d+\.\d+\.\d+\.[0-9a-f]{7}")
GITHUB_REPO_API = "https://api.github.com/repos/meshtastic/firmware"
# The REST API version of `.claude/rules/external-apis.md`.
GITHUB_API_VERSION = "2022-11-28"


def get_latest_release_info() -> dict | None:
    """Get latest Meshtastic firmware release info from GitHub API."""
    try:
        logger.info("Fetching latest release info from GitHub...")
        api_url = "https://api.github.com/repos/meshtastic/firmware/releases/latest"
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()

        data = response.json()
        version = data["tag_name"]
        published = data["published_at"]

        logger.info(f"Latest release: {version} (published: {published})")
        return {
            "version": version,
            "published_date": published,
            "release_data": data,
        }
    except Exception as e:
        logger.error(f"Error fetching release info: {e}")
        return None


class ReleaseNotFound(Exception):
    """The GitHub releases API gives no firmware version for the requested channel."""


class FirmwareVersionInstalled(Exception):
    """The `firmware_sources` table holds a ready record of the firmware version."""


class InstallActive(Exception):
    """An install of the firmware version has not ended."""

    def __init__(self, install: "Install"):
        super().__init__(install.version)
        self.install = install


class FirmwareVersionInUse(Exception):
    """A queued or running build uses the firmware version."""


class InstallFailed(Exception):
    """One step of an install failed. The message is the error that the progress stream sends."""


def github_headers() -> dict[str, str]:
    """Return the headers of each GitHub request, with the token of `GITHUB_TOKEN` when it is set."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MTFWBuilder",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    # An anonymous client gets 60 requests per hour, and a token raises that limit.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_channel(channel: str) -> str:
    """Return the newest firmware version of the channel `stable` or `alpha`, from the GitHub releases API.

    Raises `ReleaseNotFound` when no release matches, and `requests.RequestException` when GitHub fails.
    """
    if channel == "stable":
        # `releases/latest` excludes drafts and prereleases, and upstream marks each alpha as a prerelease.
        response = requests.get(f"{GITHUB_REPO_API}/releases/latest", headers=github_headers(), timeout=30)
        response.raise_for_status()
        return response.json()["tag_name"]
    url = f"{GITHUB_REPO_API}/releases"
    response = requests.get(url, params={"per_page": 100}, headers=github_headers(), timeout=30)
    response.raise_for_status()
    # Upstream adds `(Revoked)` to the release name of a revoked alpha, and keeps its prerelease flag.
    alphas = [
        release
        for release in response.json()
        if release["prerelease"]
        and not release["draft"]
        and "revoked" not in (release["name"] or "").lower()
        and TAG.fullmatch(release["tag_name"])
    ]
    if not alphas:
        raise ReleaseNotFound("GitHub lists no alpha firmware version")
    # The documentation of the list gives no order, so the version numbers decide.
    newest = max(alphas, key=lambda r: (tuple(map(int, r["tag_name"][1:].split(".")[:3])), r["published_at"]))
    return newest["tag_name"]


def download_zipball(tag: str, zip_path: Path) -> None:
    """Write the source archive of the firmware version to `zip_path`.

    Raises `requests.RequestException` when GitHub fails, and `OSError` when the disk is full.
    """
    # The zipball URL answers with a redirect to the archive, and `requests.get` follows it.
    with requests.get(f"{GITHUB_REPO_API}/zipball/{tag}", headers=github_headers(), stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)


def extract_zipball(zip_path: Path, dest: Path) -> Path:
    """Return the top directory of the archive, after the archive is extracted into `dest`.

    Raises `InstallFailed` when an entry would leave `dest` or the archive has no single top directory.
    Raises `zipfile.BadZipFile` for a damaged archive, and `OSError` when the disk is full.
    """
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for entry in names:
            # A `..` part or an absolute path in an entry name would write outside `dest` (zip slip).
            if not (root / entry).resolve().is_relative_to(root):
                raise InstallFailed(f"Zip entry would escape target directory: {entry}")
        tops = {name.split("/")[0] for name in names}
        if len(tops) != 1:
            raise InstallFailed("The source archive has no single top directory")
        zf.extractall(root)
    return root / tops.pop()


@dataclass
class Install:
    """The state of one install, which the progress stream of the install reads."""

    install_id: str
    version: str
    channel: str
    status: str = "downloading"
    error: str = ""
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    @property
    def ended(self) -> bool:
        return self.status in ("ready", "failed")

    def set_status(self, status: str, error: str = "") -> None:
        """Set the install status and wake each progress stream; an ended install keeps its install status."""
        if self.ended:
            return
        self.status = status
        self.error = error
        # A waiter holds the old event, so a set-and-replace wakes each waiter once.
        self.changed.set()
        self.changed = asyncio.Event()

    def status_event(self) -> str:
        """Return the JSON of the current install status for the progress stream."""
        return InstallStatus(
            install_id=self.install_id, version=self.version, status=self.status, error=self.error
        ).model_dump_json()

    async def wait(self) -> None:
        """Return after the install ends."""
        while not self.ended:
            await self.changed.wait()


class FirmwareInstalls:
    """Run the installs and the removals of firmware versions.

    Each install runs as a task of the event loop, so it continues when no request or progress stream is open.
    `resolver` gives the firmware version of a channel; it runs in a worker thread.
    """

    def __init__(self, settings: Settings, build_queue: BuildQueue, resolver: Callable[[str], str] = resolve_channel):
        self.settings = settings
        self.build_queue = build_queue
        self.resolver = resolver
        self._installs: dict[str, Install] = {}

    def get(self, install_id: str) -> Install | None:
        return self._installs.get(install_id)

    async def start(self, channel: str | None = None, tag: str | None = None) -> Install:
        """Return a new install of the exact tag, or of the newest firmware version of the channel.

        Raises:
        - `ReleaseNotFound` or `requests.RequestException` when the channel gives no valid firmware version.
        - `FirmwareVersionInstalled` when the firmware version is installed.
        - `InstallActive` when an install of the firmware version has not ended.
        """
        if tag is None:
            version = await asyncio.to_thread(self.resolver, channel)
            # The firmware version becomes a directory name, so a resolver result gets the same check as a tag.
            if not TAG.fullmatch(version):
                raise ReleaseNotFound(f"The {channel} release tag {version!r} is not a firmware version")
        else:
            # Only an install by channel knows the channel of the firmware version.
            version, channel = tag, "pinned"
        if await find_firmware_source(self.settings.database_path, version) is not None:
            raise FirmwareVersionInstalled(version)
        # No await is between this check and the add, so two requests cannot both start one firmware version.
        for install in self._installs.values():
            if install.version == version and not install.ended:
                raise InstallActive(install)
        install = Install(secrets.token_hex(16), version, channel)
        self._installs[install.install_id] = install
        install.task = asyncio.create_task(self._run(install))
        return install

    async def remove(self, version: str) -> None:
        """Delete the directory and the record of the installed firmware version.

        Raises `KeyError` when the firmware version is not installed, and `FirmwareVersionInUse` when a
        queued or running build uses it.
        """
        # The checks run before the wait for the firmware lock, so a refusal does not wait for a running build.
        await self._removable(version)
        async with self.build_queue.firmware_lock:
            # A build can arrive while the removal waits for the firmware lock, so the checks run again.
            record = await self._removable(version)
            await delete_firmware_source(self.settings.database_path, version)
            source_dir = Path(record["path"])
            if source_dir.exists():
                await asyncio.to_thread(shutil.rmtree, source_dir)
        logger.info(f"Removed firmware version {version}")

    async def _removable(self, version: str) -> dict:
        database_path = self.settings.database_path
        record = await find_firmware_source(database_path, version)
        if record is None:
            raise KeyError(version)
        default = await find_firmware_source(database_path)
        if version in self.build_queue.unended_firmware_versions(default["version"] if default else None):
            raise FirmwareVersionInUse(version)
        return record

    async def _run(self, install: Install) -> None:
        firmware_dir = self.settings.firmware_dir
        firmware_dir.mkdir(parents=True, exist_ok=True)
        # The staging directory is in `firmware_dir`, so the move into `<tag>/` is one rename on one file system.
        staging = Path(tempfile.mkdtemp(prefix=f".install-{install.version}-", dir=firmware_dir))
        source_dir = firmware_dir / install.version
        moved = False
        try:
            zip_path = staging / "source.zip"
            await asyncio.to_thread(download_zipball, install.version, zip_path)
            install.set_status("extracting")
            tree = await asyncio.to_thread(extract_zipball, zip_path, staging / "tree")
            zip_path.unlink()
            install.set_status("installing packages")
            await self._pio_pkg_install(tree)
            # A build reads the firmware source under this lock, so it never sees a half-written record.
            async with self.build_queue.firmware_lock:
                if source_dir.exists():
                    raise InstallFailed(f"{source_dir} exists")
                tree.rename(source_dir)
                moved = True
                record = await asyncio.to_thread(_source_record, source_dir, install.channel, datetime.now(UTC))
                await asyncio.to_thread(record_firmware_source, self.settings.database_path, record)
            install.set_status("ready")
            logger.info(f"Installed firmware version {install.version} in {source_dir}")
        except (InstallFailed, OSError, zipfile.BadZipFile, requests.RequestException) as e:
            logger.error(f"Install of {install.version} failed: {e}")
            install.set_status("failed", error=str(e))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if install.status != "ready":
                # Only a ready install has a record, so a directory without a record would never be used or removed.
                if moved:
                    shutil.rmtree(source_dir, ignore_errors=True)
                # An internal error or a cancel ends the install here, so the progress stream still ends.
                install.set_status("failed", error="The install stopped because of an internal error.")

    async def _pio_pkg_install(self, tree: Path) -> None:
        """Install the platforms, toolchains and libraries that `platformio.ini` of `tree` names.

        Raises `InstallFailed` for a non-zero exit code or the install timeout. The timeout kills pio.
        """
        timeout = self.settings.install_timeout_seconds
        process = await asyncio.create_subprocess_exec(
            "pio",
            "pkg",
            "install",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(tree),
        )
        try:
            async with asyncio.timeout(timeout):
                output, _ = await process.communicate()
        except TimeoutError as e:
            raise InstallFailed(f"pio pkg install timed out after {timeout} seconds") from e
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            tail = output.decode("utf-8", errors="replace").strip().splitlines()[-20:]
            logger.error("pio pkg install output:\n" + "\n".join(tail))
            raise InstallFailed(f"pio pkg install exited with code {process.returncode}")


async def install_events(install: Install):
    """Yield the SSE events of the progress stream for one install.

    The first event is the current install status. Then the stream sends each change, and ends after the install ends.
    """
    while True:
        # Hold the event before the read, so a change during a yield is not lost.
        changed = install.changed
        ended = install.ended
        yield {"event": "status", "data": install.status_event()}
        # The install can end during the yield, so the stream ends only after it sends the ended install status.
        if ended:
            return
        await changed.wait()


def get_firmware_version(settings: Settings) -> dict:
    """Read current firmware version info."""
    version_file = settings.base_dir / "firmware_version.txt"
    if not version_file.exists():
        return {"version": "Not installed", "last_updated": "Never"}

    lines = version_file.read_text().strip().splitlines()
    version = "Unknown"
    updated = "Unknown"
    for line in lines:
        if line.startswith("Version:"):
            version = line.replace("Version:", "").strip()
        elif line.startswith("Updated:"):
            updated = line.replace("Updated:", "").strip()

    return {"version": version, "last_updated": updated}


def import_old_firmware_source(settings: Settings) -> str | None:
    """Return the firmware version of the old firmware source, after it moves to `<firmware_dir>/<tag>/`.

    Before feature 09, the firmware source was `<firmware_dir>` itself, and `firmware_version.txt` held its
    firmware version. The import also writes the record of the firmware source. The `channel` is `stable` when
    the tag is the newest stable firmware version on GitHub, else `pinned`.

    Returns None, and changes nothing, in these cases:
    - `<firmware_dir>` holds no old firmware source.
    - The version file holds no tag.
    - `<firmware_dir>/<tag>/` exists.
    """
    firmware_dir = settings.firmware_dir
    if not (firmware_dir / "platformio.ini").is_file():
        return None
    info = get_firmware_version(settings)
    version = info["version"]
    if not TAG.fullmatch(version):
        logger.warning(f"The old firmware source stays in {firmware_dir}: {version!r} is not a firmware version")
        return None
    # An update after a skipped import installs `<tag>/` inside the old tree, and a move would nest it.
    if (firmware_dir / version).exists():
        logger.warning(f"The old firmware source stays in {firmware_dir}: {firmware_dir / version} exists")
        return None
    latest = get_latest_release_info()
    channel = "stable" if latest is not None and latest["version"] == version else "pinned"

    # Two renames in one parent directory move the tree without a copy.
    staging = firmware_dir.with_name(f"{firmware_dir.name}.import")
    firmware_dir.rename(staging)
    firmware_dir.mkdir()
    source_dir = firmware_dir / version
    staging.rename(source_dir)

    try:
        installed_at = datetime.fromisoformat(info["last_updated"]).astimezone(UTC)
    except ValueError:
        # `get_firmware_version` gives `Unknown` when the version file has no `Updated:` line.
        installed_at = datetime.now(UTC)
    record_firmware_source(settings.database_path, _source_record(source_dir, channel, installed_at))
    logger.info(f"Imported the old firmware source as {source_dir}")
    return version


def _source_record(source_dir: Path, channel: str, installed_at: datetime) -> dict:
    return {
        "version": source_dir.name,
        "channel": channel,
        "path": str(source_dir),
        "installed_at": installed_at.isoformat(timespec="seconds"),
        # `lstat` counts a symlink as itself, so a link out of the tree adds nothing.
        "size_bytes": sum(
            os.lstat(os.path.join(root, name)).st_size for root, _dirs, files in os.walk(source_dir) for name in files
        ),
        "status": "ready",
    }
