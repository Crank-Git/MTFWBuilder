"""Application configuration via pydantic-settings."""

import json
import os
from pathlib import Path
from typing import Optional

import bcrypt
import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from config.yaml and environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    firmware_dir: Optional[Path] = None
    temp_dir: Path = Path("/tmp/meshtastic_config")
    database_path: Optional[Path] = None
    devices_file: Optional[Path] = None

    # Build settings
    max_queue_size: int = 5
    build_timeout_seconds: int = 900  # 15 minutes
    cleanup_interval_seconds: int = 1800  # 30 minutes
    build_max_age_seconds: int = 3600  # 1 hour

    # Auth
    admin_password_hash: str = ""
    secret_key: str = "change-me-in-production"  # Auto-generated on config.json migration; override in config.yaml for fresh installs
    session_max_age: int = 3600  # 1 hour

    # Rate limiting
    build_rate_limit: str = "5/minute"
    login_rate_limit: str = "10/minute"

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    model_config = {"env_prefix": "MTFW_"}

    def model_post_init(self, __context) -> None:
        if self.firmware_dir is None:
            self.firmware_dir = self.base_dir / "firmware"
        if self.database_path is None:
            self.database_path = self.base_dir / "mtfwbuilder.db"
        if self.devices_file is None:
            self.devices_file = self.base_dir / "devices" / "variants.yaml"


def load_settings() -> Settings:
    """Load settings from config.yaml, with password auto-migration."""
    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / "config.yaml"
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
        import logging
        logging.getLogger("mtfwbuilder").warning(
            "SECURITY: Using default admin password 'meshtastic'. "
            "Set admin_password in config.yaml for production use."
        )

    # Auto-generate secret key if still default
    if overrides.get("secret_key", "change-me-in-production") == "change-me-in-production":
        import os as _os
        generated_key = _os.urandom(32).hex()
        overrides["secret_key"] = generated_key
        # Persist if we have a config path
        if config_path.exists() or not old_config_path.exists():
            overrides_copy = dict(overrides)
            with open(config_path, "w") as f:
                yaml.dump(overrides_copy, f, default_flow_style=False)

    return Settings(**overrides)


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
