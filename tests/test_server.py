"""Tests for FastAPI daemon and endpoints."""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from quaderno_companion.device.manager import DeviceStatus, ReadingState
from quaderno_companion.server import app

client = TestClient(app)


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


def test_cors_configuration():
    """Verify CORS middleware is configured safely without allow-credentials wildcard."""
    response = client.options(
        "/api/device/status",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == "*"
    assert response.headers.get("access-control-allow-credentials") is None


def test_open_document_validation_error():
    """Verify that invalid SSRF requests return 400 Bad Request."""
    response = client.post(
        "/api/documents/open",
        json={"url_or_path": "http://169.254.169.254/latest/meta-data/"},
    )
    assert response.status_code == 400
    assert "cloud metadata" in response.json()["detail"]


def test_api_key_authentication():
    """Verify daemon authentication when api_key is configured."""
    from quaderno_companion.config import settings

    original_key = settings.api_key
    try:
        settings.api_key = "secret-test-token"

        # 1. Missing auth header -> 401
        res = client.get("/api/viewer/status")
        assert res.status_code == 401

        # 2. Invalid auth header -> 401
        res = client.get("/api/viewer/status", headers={"X-API-Key": "wrong-token"})
        assert res.status_code == 401

        # 3. Valid X-API-Key -> success
        with patch("quaderno_companion.server.tool_get_reading_state", return_value={"status": "idle"}):
            res = client.get("/api/viewer/status", headers={"X-API-Key": "secret-test-token"})
            assert res.status_code == 200

        # 4. Valid Authorization: Bearer -> success
        with patch("quaderno_companion.server.tool_get_reading_state", return_value={"status": "idle"}):
            res = client.get("/api/viewer/status", headers={"Authorization": "Bearer secret-test-token"})
            assert res.status_code == 200
    finally:
        settings.api_key = original_key


def test_rate_limiter():
    """Verify sliding-window rate limiter behavior."""
    from quaderno_companion.server import SlidingWindowRateLimiter
    limiter = SlidingWindowRateLimiter(requests_per_minute=3)

    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is False  # 4th request within 60s blocked
    assert limiter.is_allowed("5.6.7.8") is True  # Different IP allowed


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






