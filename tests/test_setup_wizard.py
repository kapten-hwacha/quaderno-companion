"""Unit tests for Quaderno Companion Setup Wizard utilities."""

from pathlib import Path
from quaderno_companion.setup_wizard import update_env_file


def test_update_env_file_creates_and_updates(tmp_path: Path):
    """Verify update_env_file correctly writes and modifies key-value pairs."""
    env_path = tmp_path / ".env"

    # Initial write
    update_env_file(env_path, {"QUADERNO_DEVICE_IP": "192.168.1.50"})
    content = env_path.read_text()
    assert "QUADERNO_DEVICE_IP=192.168.1.50" in content

    # Modify existing and add new
    update_env_file(env_path, {"QUADERNO_DEVICE_IP": "192.168.1.100", "QUADERNO_DEFAULT_PROFILE": "A5"})
    updated = env_path.read_text()
    assert "QUADERNO_DEVICE_IP=192.168.1.100" in updated
    assert "QUADERNO_DEVICE_IP=192.168.1.50" not in updated
    assert "QUADERNO_DEFAULT_PROFILE=A5" in updated

