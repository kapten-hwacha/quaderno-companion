"""Tests for handwritten notes and mathematical formula OCR transcription pipeline."""

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf
import pytest

from quaderno_companion.config import Settings, settings
from quaderno_companion.pipeline.transcriber import (
    find_annotated_pages,
    render_page_to_png,
    transcribe_image_with_gemini,
    transcribe_pdf,
)


def create_annotated_test_pdf() -> bytes:
    """Create a 3-page test PDF where:
    - Page 1 has no annotations.
    - Page 2 has an ink stroke annotation.
    - Page 3 has a highlight annotation.
    """
    doc = pymupdf.open()

    # Page 1: Clean
    p1 = doc.new_page(width=400, height=600)
    p1.insert_text(pymupdf.Point(50, 50), "Page 1: Printed content only.")

    # Page 2: Ink annotation (handwriting)
    p2 = doc.new_page(width=400, height=600)
    p2.insert_text(pymupdf.Point(50, 50), "Page 2: Handwritten formula below.")
    annot = p2.add_ink_annot([[(100.0, 100.0), (150.0, 150.0), (200.0, 120.0)]])
    annot.update()

    # Page 3: Highlight
    p3 = doc.new_page(width=400, height=600)
    p3.insert_text(pymupdf.Point(50, 50), "Page 3: Highlighted text.")
    highlight = p3.add_highlight_annot(pymupdf.Rect(50, 45, 150, 60))
    highlight.update()

    data = doc.tobytes()
    doc.close()
    return data


def test_find_annotated_pages(tmp_path: Path):
    """Test detection of pages with annotations."""
    pdf_bytes = create_annotated_test_pdf()
    pdf_path = tmp_path / "annotated.pdf"
    pdf_path.write_bytes(pdf_bytes)

    # Test from path
    annotated = find_annotated_pages(pdf_path)
    assert annotated == [2, 3]

    # Test from bytes
    annotated_bytes = find_annotated_pages(pdf_bytes)
    assert annotated_bytes == [2, 3]


def test_render_page_to_png():
    """Test rendering page to valid PNG bytes."""
    pdf_bytes = create_annotated_test_pdf()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[1]  # Page 2
    png_bytes = render_page_to_png(page, dpi=100)
    doc.close()

    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_bytes) > 500


@pytest.mark.asyncio
async def test_transcribe_image_missing_key():
    """Test ValueError raised when no Gemini API key is available."""
    with patch.object(Settings, "resolve_gemini_api_key", return_value=None):
        with pytest.raises(ValueError, match="Gemini API key not found"):
            await transcribe_image_with_gemini(b"fake_image_bytes", api_key=None)


@pytest.mark.asyncio
async def test_transcribe_image_success():
    """Test successful Gemini API call returning Markdown with LaTeX math."""
    mock_response_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "### Notes\nFormula: $\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$"
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 258,
            "candidatesTokenCount": 35,
        },
    }

    mock_resp = httpx.Response(200, json=mock_response_data, request=httpx.Request("POST", "https://api.example.com"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await transcribe_image_with_gemini(
            b"fake_image",
            api_key="test_key_123",
            model="gemini-2.5-flash-lite",
        )

        assert "Formula:" in res["text"]
        assert "\\int_0^\\infty" in res["text"]
        assert res["model"] == "gemini-2.5-flash-lite"
        assert res["usage"]["promptTokenCount"] == 258


@pytest.mark.asyncio
async def test_transcribe_image_fallback_on_404():
    """Test fallback to next model when requested model returns 404."""
    resp_404 = httpx.Response(404, request=httpx.Request("POST", "https://api.example.com"))
    resp_200 = httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": "Fallback success."}]}}]},
        request=httpx.Request("POST", "https://api.example.com"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_404, resp_200]
        res = await transcribe_image_with_gemini(
            b"fake_image",
            api_key="test_key_123",
            model="nonexistent-model",
        )

        assert res["text"] == "Fallback success."
        assert res["model"] == "gemini-2.0-flash-lite"


@pytest.mark.asyncio
async def test_transcribe_pdf_end_to_end(tmp_path: Path):
    """Test end-to-end PDF transcription and output file generation."""
    pdf_bytes = create_annotated_test_pdf()
    pdf_file = tmp_path / "math_notes.pdf"
    pdf_file.write_bytes(pdf_bytes)

    out_file = tmp_path / "custom_output.md"

    mock_response_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Handwritten Equation: $E = mc^2$"
                        }
                    ]
                }
            }
        ]
    }
    mock_resp = httpx.Response(200, json=mock_response_data, request=httpx.Request("POST", "https://api.example.com"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await transcribe_pdf(
            pdf_path=pdf_file,
            output_path=out_file,
            only_annotated=True,
            api_key="mock_key",
        )

    assert res["status"] == "success"
    assert res["pages"] == [2, 3]
    assert out_file.exists()

    content = out_file.read_text(encoding="utf-8")
    assert "# Math Notes - Transcribed Notes" in content
    assert "## Page 2" in content
    assert "Handwritten Equation: $E = mc^2$" in content
    assert "## Page 3" in content
