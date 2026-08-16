"""Unit tests for Active Window capture and E-ink conversion."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest
import pymupdf as fitz

from quaderno_companion.triggers.window import capture_active_window_pdf, crop_to_aspect_ratio


def test_crop_to_aspect_ratio():
    """Verify crop_to_aspect_ratio creates exact target aspect ratio within 1px rounding."""
    img_wide = Image.new("RGB", (1920, 1080))
    cropped = crop_to_aspect_ratio(img_wide, 1650, 2200)
    assert abs((cropped.width / cropped.height) - (1650 / 2200)) < 0.005


def test_capture_active_window_pdf_full_screen(tmp_path):
    """Verify converting a window capture into a 100% full-screen edge-to-edge PDF with standard ISO A4 paper size."""
    dummy_png = tmp_path / "dummy_window.png"
    # Create 1920x1080 landscape window
    img = Image.new("RGB", (1920, 1080), color=(255, 255, 255))
    img.save(dummy_png)

    with patch("subprocess.run") as mock_run:
        def _fake_capture(*args, **kwargs):
            out_file = args[0][-1]
            img.save(out_file)
            return MagicMock(returncode=0)

        mock_run.side_effect = _fake_capture

        pdf_path, filename, title = capture_active_window_pdf(profile_name="A4", auto_rotate=True)

        assert pdf_path.exists()
        assert filename.startswith("Window_")
        assert len(title) > 0

        # Verify PDF page geometry matches standard ISO A4 (595.0 x 842.0 pt)
        doc = fitz.open(str(pdf_path))
        assert len(doc) == 1
        page = doc[0]
        assert round(page.rect.width, 1) == 595.0
        assert round(page.rect.height, 1) == 842.0

        # Verify image bbox spans 100% of the page (zero margins)
        img_info = page.get_image_info()[0]
        bbox = img_info["bbox"]
        assert round(bbox[0], 2) == 0.0
        assert round(bbox[1], 2) == 0.0
        assert round(bbox[2], 1) == 595.0
        assert round(bbox[3], 1) == 842.0
        doc.close()


def test_capture_active_window_pdf_portrait(tmp_path):
    """Verify portrait images are converted edge-to-edge with standard ISO A4 paper size."""
    dummy_png = tmp_path / "dummy_portrait.png"
    # Create portrait image (600 wide x 800 high)
    img = Image.new("RGB", (600, 800), color=(255, 255, 255))
    img.save(dummy_png)

    with patch("subprocess.run") as mock_run:
        def _fake_capture(*args, **kwargs):
            out_file = args[0][-1]
            img.save(out_file)
            return MagicMock(returncode=0)

        mock_run.side_effect = _fake_capture

        pdf_path, filename, title = capture_active_window_pdf(profile_name="A4", auto_rotate=True)
        assert pdf_path.exists()

        doc = fitz.open(str(pdf_path))
        assert len(doc) == 1
        page = doc[0]
        assert round(page.rect.width, 1) == 595.0
        assert round(page.rect.height, 1) == 842.0

        img_info = page.get_image_info()[0]
        bbox = img_info["bbox"]
        assert round(bbox[0], 2) == 0.0
        assert round(bbox[1], 2) == 0.0
        assert round(bbox[2], 1) == 595.0
        assert round(bbox[3], 1) == 842.0
        doc.close()
