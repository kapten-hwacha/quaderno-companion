"""Tests for FastAPI daemon and endpoints."""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from quaderno_companion.config import settings
from quaderno_companion.device.manager import DeviceStatus, ReadingState
from quaderno_companion.server import app

# Authenticated test client using daemon API key
client = TestClient(app, headers={"X-API-Key": settings.get_or_create_api_key()})


def test_healthz():
    """Verify health endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "quaderno-companion"}


def test_root_endpoint():
    """Verify root endpoint returns status info."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_device_status_endpoint():
    """Verify GET /api/device/status endpoint."""
    mock_status = DeviceStatus(
        is_connected=True,
        is_paired=True,
        connection_type="wifi",
        host="192.168.1.150",
        port=8443,
        battery_level=92,
        battery_charging=False,
        storage_total_mb=11000.0,
        storage_free_mb=9800.0,
        reading_state=ReadingState(
            document_id="doc-abc",
            title="Attention Is All You Need",
            current_page=4,
            total_pages=15,
        ),
    )

    with patch("quaderno_companion.server.device_manager.get_status", return_value=mock_status):
        response = client.get("/api/device/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_connected"] is True
        assert data["connection_type"] == "wifi"
        assert data["battery_level"] == 92
        assert data["reading_state"]["title"] == "Attention Is All You Need"
        assert data["reading_state"]["current_page"] == 4


def test_viewer_navigation_endpoint():
    """Verify POST /api/viewer/page endpoint."""
    mock_res = {
        "status": "success",
        "document_id": "doc-abc",
        "title": "Test Paper",
        "page": 5,
        "total_pages": 10,
        "action": "next",
    }

    with patch("quaderno_companion.server.tool_navigate_reader", return_value={"status": "success", "details": mock_res}):
        response = client.post("/api/viewer/page", json={"action": "next"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["details"]["page"] == 5


def test_cors_not_allowed():
    """Verify CORS wildcard headers are not exposed, preventing malicious browser cross-origin requests."""
    response = client.options(
        "/api/device/status",
        headers={"Origin": "https://malicious-website.com", "Access-Control-Request-Method": "GET"},
    )
    # Without CORS middleware, FastAPI does not return access-control-allow-origin header
    assert response.headers.get("access-control-allow-origin") is None


def test_open_document_validation_error():
    """Verify that invalid SSRF requests return 400 Bad Request."""
    response = client.post(
        "/api/documents/open",
        json={"url_or_path": "http://169.254.169.254/latest/meta-data/"},
    )
    assert response.status_code == 400
    assert "cloud metadata" in response.json()["detail"]


def test_api_key_authentication():
    """Verify daemon authentication enforcement."""
    unauthed_client = TestClient(app)

    original_key = settings.api_key
    try:
        settings.api_key = "secret-test-token"

        # 1. Missing auth header -> 401
        res = unauthed_client.get("/api/viewer/status")
        assert res.status_code == 401

        # 2. Invalid auth header -> 401
        res = unauthed_client.get("/api/viewer/status", headers={"X-API-Key": "wrong-token"})
        assert res.status_code == 401

        # 3. Valid X-API-Key -> success
        with patch("quaderno_companion.server.tool_get_reading_state", return_value={"status": "idle"}):
            res = unauthed_client.get("/api/viewer/status", headers={"X-API-Key": "secret-test-token"})
            assert res.status_code == 200

        # 4. Valid Authorization: Bearer -> success
        with patch("quaderno_companion.server.tool_get_reading_state", return_value={"status": "idle"}):
            res = unauthed_client.get("/api/viewer/status", headers={"Authorization": "Bearer secret-test-token"})
            assert res.status_code == 200
    finally:
        settings.api_key = original_key


def test_get_or_create_api_key(tmp_path):
    """Verify auto-generation and persistence of API key."""
    from quaderno_companion.config import Settings

    test_settings = Settings(config_dir=tmp_path / "config", api_key=None)
    key = test_settings.get_or_create_api_key()
    assert len(key) >= 32
    assert test_settings.api_key == key

    # Verify key was written to config .env
    env_file = tmp_path / "config" / ".env"
    assert env_file.exists()
    assert key in env_file.read_text()

    # Second call returns same key
    assert test_settings.get_or_create_api_key() == key


def test_rate_limiter():
    """Verify sliding-window rate limiter behavior and memory eviction."""
    from quaderno_companion.server import SlidingWindowRateLimiter
    import time

    limiter = SlidingWindowRateLimiter(requests_per_minute=3, max_tracked_ips=2)

    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is False  # 4th request within 60s blocked
    assert limiter.is_allowed("5.6.7.8") is True  # Different IP allowed

    # Test eviction of stale entries
    limiter._last_cleanup = 0.0  # Force cleanup trigger on next check
    # Pretend old IPs have timestamps 70s in the past
    old_time = time.time() - 70.0
    limiter._history["1.2.3.4"] = limiter._history["1.2.3.4"].__class__([old_time])
    limiter._history["5.6.7.8"] = limiter._history["5.6.7.8"].__class__([old_time])

    assert limiter.is_allowed("9.9.9.9") is True
    # Stale IPs should have been pruned
    assert "1.2.3.4" not in limiter._history
    assert "5.6.7.8" not in limiter._history


def test_sync_error_sanitized():
    """Verify /api/sync returns a generic sanitized error message on failure."""
    with patch("quaderno_companion.server.syncer.sync_pass", side_effect=RuntimeError("sensitive internal path /var/secret")):
        res = client.post("/api/sync")
        assert res.status_code == 500
        assert res.json()["detail"] == "Sync pass failed."
        assert "/var/secret" not in res.json()["detail"]


def test_cache_cleanup(tmp_path):
    """Verify cache directory cleanup based on age and total size."""
    from quaderno_companion.config import Settings
    import time

    test_settings = Settings(cache_dir=tmp_path / "cache")
    test_settings.ensure_directories()

    # Create test cache files
    file_old = test_settings.cache_dir / "old_doc.pdf"
    file_old.write_bytes(b"A" * 1000)

    # Set old timestamp (10 days old)
    old_time = time.time() - (10 * 86400)
    import os
    os.utime(file_old, (old_time, old_time))

    file_new = test_settings.cache_dir / "new_doc.pdf"
    file_new.write_bytes(b"B" * 1000)

    deleted = test_settings.clean_cache(max_age_days=7, max_total_mb=10)
    assert deleted == 1
    assert not file_old.exists()
    assert file_new.exists()


def test_sync_endpoints():
    """Verify /api/sync and /api/sync/status endpoints."""
    from quaderno_companion.fs.syncer import SyncResult

    mock_res = SyncResult(pulled=["doc1.pdf"], pushed=["doc2.pdf"])

    with patch("quaderno_companion.server.syncer.sync_pass", return_value=mock_res):
        res = client.post("/api/sync")
        assert res.status_code == 200
        data = res.json()
        assert data["pulled"] == ["doc1.pdf"]
        assert data["pushed"] == ["doc2.pdf"]

    res_status = client.get("/api/sync/status")
    assert res_status.status_code == 200
    assert "sync_dir" in res_status.json()


def test_agent_push_summarize_with_pages():
    """Verify /api/agent/push endpoint forwards pages and notebook parameters to summarize_and_push."""
    with patch("quaderno_companion.server.agent.summarize_and_push", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = {"status": "success", "message": "Summary Pushed"}

        res = client.post(
            "/api/agent/push",
            json={
                "url": "https://example.com/article",
                "title": "Article Title",
                "summarize": True,
                "pages": 2,
                "notebook_url": "https://notebooklm.google.com/notebook/xyz",
                "provider": "gemini_notebook",
            },
        )
        assert res.status_code == 200
        mock_sum.assert_called_once_with(
            "https://example.com/article",
            title="Article Title",
            pages=2,
            notebook_url="https://notebooklm.google.com/notebook/xyz",
            notebook_id=None,
            provider="gemini_notebook",
            notebook_mode=None,
            cleanup=None,
        )


def test_open_document_epub_upload():
    """Verify multipart file upload of an EPUB to /api/documents/open converts to PDF and opens."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        z.writestr("content.opf", """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Uploaded Novel</dc:title></metadata>
  <manifest><item id="ch1" href="ch1.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="ch1"/></spine>
</package>""")
        z.writestr("ch1.html", "<html><body><p>Hello uploaded EPUB</p></body></html>")
    epub_bytes = buf.getvalue()

    with patch("quaderno_companion.server.device_manager.open_document", new_callable=AsyncMock) as mock_open:
        mock_open.return_value = {"document_id": "test-doc-123", "page": 1, "total_pages": 1}

        files = {"file": ("uploaded_novel.epub", epub_bytes, "application/epub+zip")}
        res = client.post("/api/documents/open", files=files)

        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_open.assert_called_once()
        call_kwargs = mock_open.call_args.kwargs
        assert call_kwargs["filename"] == "Uploaded Novel.pdf"
        assert len(call_kwargs["pdf_bytes"]) > 0


def test_list_fs_folders_endpoint():
    """Verify GET /api/fs/folders endpoint returns available folders."""
    with patch("quaderno_companion.server.device_manager.get_available_folders", new_callable=AsyncMock) as mock_folders:
        mock_folders.return_value = ["Document", "Document/Companion", "Document/Research"]
        res = client.get("/api/fs/folders")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "Document/Research" in data["folders"]

        res_v1 = client.get("/api/v1/fs/folders")
        assert res_v1.status_code == 200


def test_open_document_with_destination_folder():
    """Verify POST /api/documents/open respects custom destination folder."""
    with patch("quaderno_companion.server.tool_push_document", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "success", "message": "Opened document"}
        res = client.post(
            "/api/documents/open",
            json={
                "url_or_path": "https://example.com/test.pdf",
                "folder": "Document/Research",
            },
        )
        assert res.status_code == 200
        mock_push.assert_called_once_with(
            source_url_or_path="https://example.com/test.pdf",
            title=None,
            page=1,
            profile=None,
            destination_folder="Document/Research",
        )







