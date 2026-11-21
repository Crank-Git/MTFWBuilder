"""Tests that keep the suite out of the repository root."""

from mtfwbuilder.auth import verify_password
from mtfwbuilder.config import load_settings
from tests.conftest import REPO_ROOT, SECRET_KEY


def _root_files():
    """Return the name and modification time of each file in the repository root."""
    return {p.name: p.stat().st_mtime_ns for p in REPO_ROOT.iterdir() if p.is_file()}


def test_load_settings_writes_no_file_to_repository_root(settings_env):
    before = _root_files()
    load_settings()
    assert _root_files() == before


def test_load_settings_reads_config_yaml_from_base_dir(settings_env, admin_password):
    settings = load_settings()
    assert settings.base_dir == settings_env
    assert settings.secret_key == SECRET_KEY
    assert verify_password(admin_password, settings.admin_password_hash)
