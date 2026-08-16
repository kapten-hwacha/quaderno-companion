"""Unit tests for Native Browser Active Tab Detection."""

import json
from unittest.mock import patch
import lz4.block
import pytest

from quaderno_companion.triggers.browser import (
    MOZ_HEADER,
    _decompress_mozlz4,
    get_active_browser_tab,
    get_firefox_active_tab,
)


def test_decompress_mozlz4_valid():
    """Verify decompression of Mozilla proprietary LZ4 format."""
    original_dict = {"windows": [{"tabs": [{"entries": [{"url": "https://example.com", "title": "Example"}]}]}]}
    raw_json = json.dumps(original_dict).encode("utf-8")
    compressed = lz4.block.compress(raw_json)
    moz_payload = MOZ_HEADER + compressed

    decompressed = _decompress_mozlz4(moz_payload)
    assert decompressed == original_dict


def test_decompress_mozlz4_invalid_header():
    """Verify invalid header returns None."""
    assert _decompress_mozlz4(b"invalid_header_data") is None


def test_firefox_active_tab_extraction(tmp_path):
    """Verify extracting the selected active tab from Firefox session store."""
    profile_dir = tmp_path / "test_profile.default"
    backup_dir = profile_dir / "sessionstore-backups"
    backup_dir.mkdir(parents=True)
    recovery_file = backup_dir / "recovery.jsonlz4"

    session_data = {
        "windows": [
            {
                "selected": 2,
                "tabs": [
                    {
                        "entries": [{"url": "https://first-tab.org", "title": "First Tab"}],
                        "index": 1,
                    },
                    {
                        "entries": [
                            {"url": "https://old-history.org", "title": "Old Page"},
                            {"url": "https://en.wikipedia.org/wiki/E-ink", "title": "E-ink - Wikipedia"},
                        ],
                        "index": 2,
                    },
                ],
            }
        ]
    }

    raw_json = json.dumps(session_data).encode("utf-8")
    recovery_file.write_bytes(MOZ_HEADER + lz4.block.compress(raw_json))

    with patch("quaderno_companion.triggers.browser.Path.home", return_value=tmp_path.parent):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=[recovery_file]):
                tab = get_firefox_active_tab()
                assert tab is not None
                assert tab["browser"] == "Firefox"
                assert tab["title"] == "E-ink - Wikipedia"
                assert tab["url"] == "https://en.wikipedia.org/wiki/E-ink"


def test_get_active_browser_tab_firefox_fallback():
    """Verify universal get_active_browser_tab detects Firefox when no frontmost override."""
    fake_tab = {"browser": "Firefox", "title": "Test Title", "url": "https://test.com"}
    with patch("quaderno_companion.triggers.browser.get_frontmost_app_name", return_value="Terminal"):
        with patch("quaderno_companion.triggers.browser.get_firefox_active_tab", return_value=fake_tab):
            res = get_active_browser_tab()
            assert res == fake_tab
