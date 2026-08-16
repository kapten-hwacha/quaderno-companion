"""Unit tests for Quaderno Companion Setup Wizard."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from quaderno_companion.setup_wizard import (
    run_api_setup_wizard,
    update_env_file,
    verify_gemini_api_key,
)


def test_update_env_file_creates_and_updates(tmp_path: Path):
    """Verify update_env_file correctly writes and modifies key-value pairs."""
    env_path = tmp_path / ".env"

    # Initial write
    update_env_file(env_path, {"GEMINI_API_KEY": "key123", "QUADERNO_DEVICE_IP": "192.168.1.50"})
    content = env_path.read_text()
    assert "GEMINI_API_KEY=key123" in content
    assert "QUADERNO_DEVICE_IP=192.168.1.50" in content

    # Modify existing and add new
    update_env_file(env_path, {"GEMINI_API_KEY": "newkey456", "QUADERNO_LLM_MODEL": "gemini-2.5-flash"})
    updated = env_path.read_text()
    assert "GEMINI_API_KEY=newkey456" in updated
    assert "GEMINI_API_KEY=key123" not in updated
    assert "QUADERNO_DEVICE_IP=192.168.1.50" in updated
    assert "QUADERNO_LLM_MODEL=gemini-2.5-flash" in updated


def test_verify_gemini_api_key_short_key():
    """Verify short or empty key returns false immediately."""
    ok, msg = verify_gemini_api_key("")
    assert ok is False
    assert "too short" in msg

    ok2, msg2 = verify_gemini_api_key("123")
    assert ok2 is False


def test_verify_gemini_api_key_success():
    """Verify valid response from Gemini API returns success."""
    mock_resp = MagicMock()
    mock_resp.is_success = True

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        ok, msg = verify_gemini_api_key("valid-ai-key-1234567890", model="gemini-3.5-flash-lite")
        assert ok is True
        assert "valid and active" in msg


def test_verify_gemini_api_key_invalid():
    """Verify 400 Bad Request maps to invalid key message."""
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 400

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        ok, msg = verify_gemini_api_key("invalid-key-1234567890")
        assert ok is False
        assert "Invalid API key" in msg


def test_run_api_setup_wizard_success(tmp_path: Path):
    """Verify run_api_setup_wizard writes configuration when verified."""
    target_env = tmp_path / ".env"
    global_env = tmp_path / "global.env"

    with patch("quaderno_companion.setup_wizard.verify_gemini_api_key", return_value=(True, "Key is valid!")):
        success = run_api_setup_wizard(
            api_key="AIzaSyTestKey1234567890",
            model="gemini-3.5-flash-lite",
            target_env=target_env,
            global_env=global_env,
            verify=True,
        )
        assert success is True
        assert target_env.exists()
        assert global_env.exists()
        content = target_env.read_text()
        assert "GEMINI_API_KEY=AIzaSyTestKey1234567890" in content
        assert "QUADERNO_LLM_MODEL=gemini-3.5-flash-lite" in content
        assert "QUADERNO_SUMMARIZER_PROVIDER=gemini_api" in content
