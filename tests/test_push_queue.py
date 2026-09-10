"""Tests for Offline Push Queue Manager, CLI, and Daemon Integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from quaderno_companion.config import settings
from quaderno_companion.device.client import DeviceNotConnectedError
from quaderno_companion.push_queue import PushQueueManager


@pytest.fixture
def temp_queue(tmp_path):
    """Isolate queue storage and staging directory for testing."""
    original_config = settings.config_dir
    original_cache = settings.cache_dir
    settings.config_dir = tmp_path / "config"
    settings.cache_dir = tmp_path / "cache"
    settings.ensure_directories()

    manager = PushQueueManager()

    yield manager

    # Cleanup
    settings.config_dir = original_config
    settings.cache_dir = original_cache


def test_enqueue_bytes_and_list(temp_queue):
    """Verify queuing PDF bytes saves staged file and updates queue list."""
    dummy_pdf = b"%PDF-1.4 dummy content"
    item = temp_queue.enqueue_bytes(
        pdf_bytes=dummy_pdf,
        filename="research_paper.pdf",
        title="Research Paper",
        page=3,
        destination_folder="Document/Research",
    )

    assert item.title == "Research Paper"
    assert item.filename == "research_paper.pdf"
    assert item.remote_folder == "Document/Research"
    assert item.page == 3
    assert item.file_size == len(dummy_pdf)
    assert Path(item.staged_path).exists()
    assert Path(item.staged_path).read_bytes() == dummy_pdf

    items = temp_queue.list_items()
    assert len(items) == 1
    assert items[0].id == item.id
    assert temp_queue.count() == 1
    assert temp_queue.has_items()


def test_queue_remove_and_clear(temp_queue):
    """Verify removing single item and clearing entire queue cleans up files."""
    dummy_pdf = b"%PDF-1.4 sample"
    item1 = temp_queue.enqueue_bytes(dummy_pdf, "doc1.pdf", title="Doc 1")
    item2 = temp_queue.enqueue_bytes(dummy_pdf, "doc2.pdf", title="Doc 2")

    assert temp_queue.count() == 2
    staged1 = Path(item1.staged_path)
    staged2 = Path(item2.staged_path)
    assert staged1.exists() and staged2.exists()

    # Remove item1
    assert temp_queue.remove(item1.id) is True
    assert not staged1.exists()
    assert temp_queue.count() == 1
    assert temp_queue.list_items()[0].id == item2.id

    # Clear queue
    cleared = temp_queue.clear()
    assert cleared == 1
    assert not staged2.exists()
    assert temp_queue.count() == 0
    assert not temp_queue.has_items()


def test_flush_when_disconnected(temp_queue):
    """Verify flush cleanly aborts when Quaderno device is offline."""
    dummy_pdf = b"%PDF-1.4 test"
    temp_queue.enqueue_bytes(dummy_pdf, "doc.pdf", title="Doc")

    mock_mgr = MagicMock()
    mock_mgr.get_client_sync.side_effect = DeviceNotConnectedError("Device offline")

    res = temp_queue.flush_sync(client=None, device_mgr=mock_mgr)
    assert res.device_connected is False
    assert len(res.flushed) == 0
    assert res.remaining == 1
    assert temp_queue.count() == 1


def test_flush_when_connected(temp_queue):
    """Verify flush uploads all items in FIFO order and displays the last item."""
    dummy_pdf1 = b"%PDF-1.4 file 1"
    dummy_pdf2 = b"%PDF-1.4 file 2"
    item1 = temp_queue.enqueue_bytes(dummy_pdf1, "first.pdf", title="First", page=1)
    item2 = temp_queue.enqueue_bytes(dummy_pdf2, "second.pdf", title="Second", page=5)

    mock_client = MagicMock()
    mock_client.upload_document_sync.side_effect = ["id-111", "id-222"]

    mock_mgr = MagicMock()

    res = temp_queue.flush_sync(client=mock_client, device_mgr=mock_mgr)

    assert res.device_connected is True
    assert len(res.flushed) == 2
    assert res.remaining == 0
    assert temp_queue.count() == 0

    # Verify upload was called for both
    assert mock_client.upload_document_sync.call_count == 2

    # Verify display was called for the last document
    mock_client.display_document_sync.assert_called_once_with(document_id="id-222", page=5)


@pytest.mark.asyncio
async def test_async_enqueue_and_flush(temp_queue):
    """Verify async enqueue and async flush work properly."""
    with patch("quaderno_companion.pipeline.fetcher.ContentFetcher.fetch") as mock_fetch:
        from quaderno_companion.pipeline.fetcher import FetchedDocument
        mock_fetch.return_value = FetchedDocument(
            title="Async Doc",
            pdf_bytes=b"%PDF-1.4 async",
            filename="async_doc.pdf",
        )

        item = await temp_queue.enqueue(source_url_or_path="https://example.com/test", title="Async Doc")
        assert item.title == "Async Doc"
        assert temp_queue.count() == 1

        mock_client = MagicMock()
        mock_client.upload_document_sync.return_value = "id-async"
        mock_mgr = MagicMock()

        res = await temp_queue.flush(client=mock_client, device_mgr=mock_mgr)
        assert len(res.flushed) == 1
        assert temp_queue.count() == 0


def test_cli_queue_commands(tmp_path):
    """Verify quadctl queue list, clear, remove CLI commands."""
    from quaderno_companion.cli import app
    from quaderno_companion.push_queue import push_queue

    original_config = settings.config_dir
    original_cache = settings.cache_dir
    settings.config_dir = tmp_path / "config"
    settings.cache_dir = tmp_path / "cache"
    settings.ensure_directories()

    runner = CliRunner()

    try:
        # Initially empty
        res = runner.invoke(app, ["queue", "list"])
        assert res.exit_code == 0
        assert "empty" in res.output.lower()

        # Enqueue dummy item
        item = push_queue.enqueue_bytes(b"%PDF dummy", "sample.pdf", title="Sample Doc")

        res = runner.invoke(app, ["queue", "list"])
        assert res.exit_code == 0
        assert "Sample Doc" in res.output

        # Clear queue
        res = runner.invoke(app, ["queue", "clear"])
        assert res.exit_code == 0
        assert "Cleared 1 document" in res.output

        res = runner.invoke(app, ["queue", "list"])
        assert "empty" in res.output.lower()
    finally:
        settings.config_dir = original_config
        settings.cache_dir = original_cache


def test_server_queue_endpoints(tmp_path):
    """Verify /api/queue REST endpoints."""
    from fastapi.testclient import TestClient
    from quaderno_companion.server import app
    from quaderno_companion.push_queue import push_queue

    original_config = settings.config_dir
    original_cache = settings.cache_dir
    settings.config_dir = tmp_path / "config"
    settings.cache_dir = tmp_path / "cache"
    settings.ensure_directories()

    client = TestClient(app)
    api_key = settings.get_or_create_api_key()
    headers = {"X-API-Key": api_key}

    try:
        # 1. Check empty queue
        resp = client.get("/api/queue", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # 2. Add an item directly via push_queue
        item = push_queue.enqueue_bytes(b"%PDF test", "test_file.pdf", title="Test File")

        resp = client.get("/api/queue", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["title"] == "Test File"

        # 3. Delete specific item
        resp = client.delete(f"/api/queue/{item.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        resp = client.get("/api/queue", headers=headers)
        assert resp.json()["count"] == 0

        # 4. Enqueue and clear all
        push_queue.enqueue_bytes(b"%PDF test", "test2.pdf", title="Test 2")
        resp = client.delete("/api/queue", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 1
    finally:
        settings.config_dir = original_config
        settings.cache_dir = original_cache


def test_server_open_offline_auto_queues(tmp_path):
    """Verify that posting a document to /api/documents/open when device is offline queues it."""
    from fastapi.testclient import TestClient
    from quaderno_companion.server import app
    from quaderno_companion.push_queue import push_queue

    original_config = settings.config_dir
    original_cache = settings.cache_dir
    settings.config_dir = tmp_path / "config"
    settings.cache_dir = tmp_path / "cache"
    settings.ensure_directories()

    client = TestClient(app)
    api_key = settings.get_or_create_api_key()
    headers = {"X-API-Key": api_key}

    import pymupdf
    pdf_doc = pymupdf.open()
    pdf_doc.new_page()
    dummy_pdf = tmp_path / "offline_paper.pdf"
    dummy_pdf.write_bytes(pdf_doc.convert_to_pdf())
    pdf_doc.close()

    try:
        with patch("quaderno_companion.server.tool_push_document", side_effect=DeviceNotConnectedError("Device disconnected")):
            resp = client.post(
                "/api/documents/open",
                headers=headers,
                json={"url_or_path": str(dummy_pdf), "title": "Offline Paper"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "queued"
            assert "Offline Paper" in data["message"]
            assert push_queue.count() == 1
    finally:
        settings.config_dir = original_config
        settings.cache_dir = original_cache
