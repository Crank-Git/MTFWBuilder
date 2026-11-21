"""Application configuration via pydantic-settings."""

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import bcrypt
import yaml
from pydantic import Field, IPvAnyNetwork, field_validator
from pydantic_settings import BaseSettings, NoDecode

from mtfwbuilder.services.cleanup_service import scrub_after_restart

logger = logging.getLogger("mtfwbuilder")


class DataDirError(RuntimeError):
    """The data directory does not exist and cannot be made, or the process cannot write to it."""


class Settings(BaseSettings):
    """Application settings loaded from config.yaml and environment variables."""

    debug: bool = False

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path | None = None
    firmware_dir: Path | None = None
    temp_dir: Path | None = None
    database_path: Path | None = None

    # Build settings
    max_queue_size: int = 5
    build_workers: int = Field(default=1, ge=1)
    build_timeout_seconds: int = 900  # 15 minutes
    cleanup_interval_seconds: int = 1800  # 30 minutes
    build_max_age_seconds: int = 3600  # 1 hour
    install_timeout_seconds: int = 3600  # The time limit of `pio pkg install` for one firmware version
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400  # The build cache removes an entry one day after its last use
    max_fleet_size: int = Field(default=50, ge=1)

    # Auth
    admin_password_hash: str = ""
    secret_key: str = (
        "change-me-in-production"  # Auto-generated on config.json migration; override in config.yaml for fresh installs
    )
    session_max_age: int = 3600  # 1 hour

    # Rate limiting
    build_rate_limit: str = "5/minute"
    login_rate_limit: str = "10/minute"
    trusted_proxies: Annotated[list[IPvAnyNetwork], NoDecode] = []

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # The push config page uses a test device only when this is on. Only the E2E test config turns it on.
    push_test_hooks: bool = False

    model_config = {"env_prefix": "MTFW_"}

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_proxy_list(cls, value):
        # An operator writes MTFW_TRUSTED_PROXIES as a comma-separated list, not as JSON.
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def model_post_init(self, __context) -> None:
        if self.data_dir is None:
            self.data_dir = self.base_dir / "data"
        if self.firmware_dir is None:
            self.firmware_dir = self.data_dir / "firmware"
        if self.temp_dir is None:
            self.temp_dir = self.data_dir / "artifacts"
        if self.database_path is None:
            self.database_path = self.data_dir / "mtfwbuilder.db"


def prepare_data_dir(data_dir: Path, base_dir: Path) -> None:
    """Make the data directory writable and move the `config.yaml` of an old release into it.

    Releases before the data directory kept their files in `base_dir`. A file moves only when the data
    directory has no file of that name.

    Raises:
        DataDirError: The process cannot make the data directory or write to it.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tempfile.TemporaryFile(dir=data_dir).close()
    except OSError as exc:
        raise DataDirError(
            f"The data directory {data_dir} is not writable ({exc.strerror}). "
            "Set MTFW_DATA_DIR to a writable directory, or give the process user write access to it."
        ) from exc

    if data_dir.resolve() != base_dir.resolve():
        _move_old(base_dir / "config.yaml", data_dir / "config.yaml")


def move_old_state(settings: Settings, base_dir: Path) -> None:
    """Move the database and the firmware source of an old release from `base_dir` into the data directory.

    A path moves only when its setting has the default value under the data directory. An old firmware source
    that does not move stays, but the function scrubs it, because it can hold the secrets of its last build.

    Raises:
        OSError: A scrub of the old firmware source fails.
    """
    data_dir = settings.data_dir
    if data_dir.resolve() == base_dir.resolve():
        return
    if settings.database_path == data_dir / "mtfwbuilder.db":
        _move_old(base_dir / "mtfwbuilder.db", settings.database_path)

    old_firmware = base_dir / "firmware"
    if settings.firmware_dir == data_dir / "firmware" and _move_old(old_firmware, settings.firmware_dir):
        _move_old(base_dir / "firmware_version.txt", data_dir / "firmware_version.txt")
    elif old_firmware.is_dir():
        scrub_after_restart(old_firmware)
        logger.warning(
            f"Scrubbed the old firmware source {old_firmware} and kept it, because the firmware source is "
            f"{settings.firmware_dir}. Delete {old_firmware} when you do not need it."
        )


def _move_old(old: Path, new: Path) -> bool:
    """Move `old` to `new` and return True, or return False when `old` does not exist or `new` exists."""
    if not old.exists() or new.exists():
        return False
    shutil.move(old, new)
    # Startup configures logging after the settings load, and the default level drops an info line.
    logger.warning(f"Moved {old} to {new}")
    return True


def load_settings() -> Settings:
    """Load settings from config.yaml, with password auto-migration.

    The function reads config.yaml from the data directory: `MTFW_DATA_DIR`, else `data/` under
    the base directory. It reads config.json from the base directory: `MTFW_BASE_DIR`, else the
    project root.

    Raises:
        DataDirError: The process cannot write to the data directory.
    """
    base_dir = Path(os.environ.get("MTFW_BASE_DIR") or Path(__file__).resolve().parent.parent)
    data_dir = Path(os.environ.get("MTFW_DATA_DIR") or base_dir / "data")
    prepare_data_dir(data_dir, base_dir)
    config_path = data_dir / "config.yaml"
    old_config_path = base_dir / "config.json"

    overrides: dict = {}

    # Try config.yaml first
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        overrides = data

    # Fall back to old config.json for migration
    elif old_config_path.exists():
        with open(old_config_path) as f:
            data = json.load(f)

        raw_password = data.get("admin_password", "meshtastic")
        hashed = _hash_password(raw_password)
        overrides["admin_password_hash"] = hashed

        # Write migrated config.yaml
        migrated = {"admin_password_hash": hashed, "secret_key": os.urandom(32).hex()}
        with open(config_path, "w") as f:
            yaml.dump(migrated, f, default_flow_style=False)

    # Auto-migrate plaintext passwords in config.yaml
    if "admin_password" in overrides:
        raw = overrides.pop("admin_password")
        hashed = _hash_password(raw)
        overrides["admin_password_hash"] = hashed
        # Rewrite config.yaml without plaintext
        overrides_copy = dict(overrides)
        with open(config_path, "w") as f:
            yaml.dump(overrides_copy, f, default_flow_style=False)

    # Default password if nothing configured
    if not overrides.get("admin_password_hash"):
        overrides["admin_password_hash"] = _hash_password("meshtastic")
        logger.warning(
            "SECURITY: Using default admin password 'meshtastic'. Set admin_password in config.yaml for production use."
        )

    # Auto-generate secret key if still default
    if overrides.get("secret_key", "change-me-in-production") == "change-me-in-production":
        generated_key = os.urandom(32).hex()
        overrides["secret_key"] = generated_key
        # Persist if we have a config path
        if config_path.exists() or not old_config_path.exists():
            overrides_copy = dict(overrides)
            with open(config_path, "w") as f:
                yaml.dump(overrides_copy, f, default_flow_style=False)

    # The data directory holds config.yaml, so config.yaml cannot move it.
    overrides["data_dir"] = data_dir
    settings = Settings(**overrides)
    move_old_state(settings, base_dir)
    return settings


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
