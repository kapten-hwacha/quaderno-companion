"""Tests for clipboard image capture and system pasteboard integration."""

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pymupdf

from quaderno_companion.config import settings
from quaderno_companion.device.manager import ReadingState
from quaderno_companion.triggers.clipboard import (
    copy_active_page_to_clipboard,
    get_active_page_image,
    set_clipboard_image,
)


def _create_sample_pdf(page_count: int = 3) -> bytes:
    """Helper to create an in-memory sample PDF with text on each page."""
    doc = pymupdf.open()
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 100), f"Sample Page {i + 1}", fontsize=24)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_set_clipboard_image_empty():
    """Verify empty bytes raise ValueError."""
    with pytest.raises(ValueError, match="Image bytes cannot be empty"):
        set_clipboard_image(b"")


def test_set_clipboard_image_darwin():
    """Verify macOS clipboard write via AppKit."""
    sample_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR..."
    with patch("sys.platform", "darwin"):
        mock_appkit = MagicMock()
        mock_pb = MagicMock()
        mock_appkit.NSPasteboard.generalPasteboard.return_value = mock_pb
        mock_appkit.NSImage.alloc.return_value.initWithData_.return_value = MagicMock()
        mock_pb.writeObjects_.return_value = True

        with patch.dict("sys.modules", {"AppKit": mock_appkit}):
            res = set_clipboard_image(sample_png, image_format="png")
            assert res is True
            mock_pb.clearContents.assert_called_once()
            mock_pb.writeObjects_.assert_called_once()


@pytest.mark.asyncio
async def test_get_active_page_image_from_screen():
    """Verify live screen capture when from_screen=True."""
    mock_client = MagicMock()
    mock_client.take_screenshot = AsyncMock(return_value=b"\xff\xd8\xff\xe0JPEG_DATA")

    with patch("quaderno_companion.device.manager.device_manager.get_client", new_callable=AsyncMock) as mock_get_client, \
         patch("quaderno_companion.device.manager.device_manager.get_status", new_callable=AsyncMock) as mock_get_status:
        mock_get_client.return_value = mock_client
        mock_status = MagicMock()
        mock_status.reading_state = ReadingState(document_id="doc-1", title="ScreenDoc", current_page=2, total_pages=5)
        mock_get_status.return_value = mock_status

        img_bytes, fmt, meta = await get_active_page_image(from_screen=True)

        assert img_bytes == b"\xff\xd8\xff\xe0JPEG_DATA"
        assert fmt == "jpeg"
        assert meta["from_screen"] is True
        assert meta["title"] == "ScreenDoc"
        mock_client.take_screenshot.assert_called_once()


@pytest.mark.asyncio
async def test_get_active_page_image_fetches_newest_from_device(tmp_path):
    """Verify that get_active_page_image fetches the newest document from device even if local file exists."""
    stale_pdf = _create_sample_pdf(page_count=1)
    new_pdf = _create_sample_pdf(page_count=3)
    doc_title = "AnnotatedDoc"

    # Write stale PDF to local sync folder
    settings.sync_dir = tmp_path
    local_pdf = tmp_path / f"{doc_title}.pdf"
    local_pdf.write_bytes(stale_pdf)

    state = ReadingState(document_id="doc-active-123", title=doc_title, current_page=2, total_pages=3)
    mock_client = MagicMock()
    mock_client.download_document_async = AsyncMock(return_value=new_pdf)

    with patch("quaderno_companion.device.manager.device_manager._reading_state", state), \
         patch("quaderno_companion.device.manager.device_manager.get_status", new_callable=AsyncMock) as mock_get_status, \
         patch("quaderno_companion.device.manager.device_manager.get_client", new_callable=AsyncMock) as mock_get_client:
        mock_status = MagicMock()
        mock_status.reading_state = state
        mock_get_status.return_value = mock_status
        mock_get_client.return_value = mock_client

        img_bytes, fmt, meta = await get_active_page_image(dpi=150)

        assert fmt == "png"
        assert meta["title"] == doc_title
        assert meta["page"] == 2
        assert meta["total_pages"] == 3
        assert meta["from_screen"] is False

        # Verify device was queried for newest version
        mock_client.download_document_async.assert_called_once_with("doc-active-123")

        # Verify local file was refreshed with the newest downloaded bytes
        assert local_pdf.read_bytes() == new_pdf


@pytest.mark.asyncio
async def test_get_active_page_image_pdf_render(tmp_path):
    """Verify high-resolution PDF page rendering fallback from local sync folder when device offline."""
    pdf_bytes = _create_sample_pdf(page_count=3)
    doc_title = "TestActiveDoc"
    
    # Save PDF in simulated sync_dir
    settings.sync_dir = tmp_path
    pdf_file = tmp_path / f"{doc_title}.pdf"
    pdf_file.write_bytes(pdf_bytes)

    state = ReadingState(document_id="doc-999", title=doc_title, current_page=2, total_pages=3)

    with patch("quaderno_companion.device.manager.device_manager._reading_state", state), \
         patch("quaderno_companion.device.manager.device_manager.get_status", new_callable=AsyncMock) as mock_get_status, \
         patch("quaderno_companion.device.manager.device_manager.get_client", new_callable=AsyncMock) as mock_get_client:
        mock_status = MagicMock()
        mock_status.reading_state = state
        mock_get_status.return_value = mock_status
        mock_get_client.return_value = None

        # Render page 2 (current page)
        img_bytes, fmt, meta = await get_active_page_image(dpi=150)

        assert fmt == "png"
        assert img_bytes.startswith(b"\x89PNG")
        assert meta["title"] == doc_title
        assert meta["page"] == 2
        assert meta["total_pages"] == 3
        assert meta["from_screen"] is False

        # Render specific page override (page 1)
        img_bytes_p1, fmt_p1, meta_p1 = await get_active_page_image(page=1, dpi=150)
        assert meta_p1["page"] == 1
        assert img_bytes_p1 != img_bytes


@pytest.mark.asyncio
async def test_copy_active_page_to_clipboard_end_to_end(tmp_path):
    """Verify copy_active_page_to_clipboard calls get_active_page_image and set_clipboard_image."""
    pdf_bytes = _create_sample_pdf(page_count=2)
    doc_title = "MyNotes"
    settings.sync_dir = tmp_path
    (tmp_path / f"{doc_title}.pdf").write_bytes(pdf_bytes)

    state = ReadingState(document_id="doc-xyz", title=doc_title, current_page=1, total_pages=2)

    with patch("quaderno_companion.device.manager.device_manager._reading_state", state), \
         patch("quaderno_companion.device.manager.device_manager.get_status", new_callable=AsyncMock) as mock_status, \
         patch("quaderno_companion.device.manager.device_manager.get_client", new_callable=AsyncMock) as mock_get_client, \
         patch("quaderno_companion.triggers.clipboard.set_clipboard_image", return_value=True) as mock_set_clip:
        mock_st = MagicMock()
        mock_st.reading_state = state
        mock_status.return_value = mock_st
        mock_get_client.return_value = None

        res = await copy_active_page_to_clipboard()

        assert res["status"] == "success"
        assert res["title"] == doc_title
        assert res["page"] == 1
        assert res["format"] == "png"
        assert res["size_bytes"] > 0
        mock_set_clip.assert_called_once()


def test_cli_copy_page():
    """Verify typer CLI quadctl copy-page command."""
    from typer.testing import CliRunner
    from quaderno_companion.cli import app

    runner = CliRunner()
    mock_res = {
        "status": "success",
        "title": "CliDoc",
        "page": 2,
        "total_pages": 8,
        "format": "png",
        "size_bytes": 2048,
    }

    with patch("quaderno_companion.triggers.clipboard.copy_active_page_to_clipboard", new_callable=AsyncMock) as mock_copy:
        mock_copy.return_value = mock_res
        res = runner.invoke(app, ["copy-page", "--page", "2"])
        assert res.exit_code == 0
        assert "Copied to clipboard" in res.stdout
        assert "CliDoc" in res.stdout
        mock_copy.assert_called_once_with(page=2, dpi=200, from_screen=False)

