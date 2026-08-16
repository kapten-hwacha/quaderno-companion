import pymupdf as fitz
import pytest
from quaderno_companion.config import SCREEN_PROFILES
from quaderno_companion.pipeline.optimizer import EinkOptimizer, optimize_pdf_for_eink
from quaderno_companion.pipeline.templates import EinkDocumentBuilder


def create_sample_pdf() -> bytes:
    """Generate a simple vector PDF with margins for testing."""
    doc = fitz.open()
    # A4 standard portrait page: 595 x 842 pt
    page = doc.new_page(width=595, height=842)
    
    # Add content inside a portrait bounding box
    rect = fitz.Rect(100, 150, 450, 700)
    page.insert_textbox(rect, "Quaderno Companion E-Ink Testing Content\nLine 2 with detailed reading text.")
    page.draw_rect(fitz.Rect(120, 200, 400, 650), color=(0.2, 0.4, 0.8), fill=(0.9, 0.9, 0.9))
    
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_optimizer_rescaling_and_margins():
    """Verify that optimizer scales page to target Quaderno dimensions."""
    sample_pdf = create_sample_pdf()
    
    # Test A4 profile
    optimizer_a4 = EinkOptimizer(profile_name="A4")
    out_a4_bytes = optimizer_a4.optimize_pdf(sample_pdf, trim_margins=True)
    
    doc_a4 = fitz.open(stream=out_a4_bytes, filetype="pdf")
    page_a4 = doc_a4[0]
    
    # Target points: Standard ISO A4 (595 x 842 pt)
    assert abs(page_a4.rect.width - 595.0) < 1.0
    assert abs(page_a4.rect.height - 842.0) < 1.0
    doc_a4.close()


def test_optimizer_dithering_mode():
    """Verify raster 1-bit dithering mode produces a valid PDF."""
    sample_pdf = create_sample_pdf()
    optimizer_a5 = EinkOptimizer(profile_name="A5")
    
    out_bytes = optimizer_a5.optimize_pdf(sample_pdf, dither_raster=True)
    assert len(out_bytes) > 0
    
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) == 1
    page = doc[0]
    assert abs(page.rect.width - 420.0) < 1.0
    assert abs(page.rect.height - 595.0) < 1.0
    doc.close()


def test_payload_size_compression():
    """Verify that output file sizes remain lightweight (< 300 KB)."""
    builder = EinkDocumentBuilder(profile_name="A4")
    summary_pdf = builder.render_summary_pdf(
        title="Deep Learning and Neural Architectures",
        source_url="https://arxiv.org/abs/2301.00000",
        key_takeaways=[
            "Transformers scale predictably with compute and dataset size.",
            "Attention mechanisms enable dense associative memory retrieval.",
            "Zero-friction reading pipelines improve knowledge retention.",
        ],
        sections={
            "Background": "E-ink screens require high contrast and minimal payload overhead.",
            "Methodology": "We utilize PyMuPDF vector mapping and margin trimming.",
        },
    )
    
    optimized = optimize_pdf_for_eink(summary_pdf, profile="A4")
    # File size should be well under 300 KB (typically 5-30 KB for vector summaries)
    assert len(optimized) < 300 * 1024


def test_multipage_uniform_crop_dimensions():
    """Verify that multi-page documents maintain consistent crop size across pages."""
    doc = fitz.open()
    # Page 1: Full content
    p1 = doc.new_page(width=595, height=842)
    p1.draw_rect(fitz.Rect(100, 100, 500, 700), fill=(0.9, 0.9, 0.9))

    # Page 2: Short paragraph
    p2 = doc.new_page(width=595, height=842)
    p2.draw_rect(fitz.Rect(100, 100, 500, 250), fill=(0.9, 0.9, 0.9))

    pdf_bytes = doc.tobytes()
    doc.close()

    optimizer = EinkOptimizer(profile_name="A4")
    out = optimizer.optimize_pdf(pdf_bytes, trim_margins=True)

    out_doc = fitz.open(stream=out, filetype="pdf")
    assert len(out_doc) == 2
    # Both pages should render to standard A4 target dimensions
    assert out_doc[0].rect == out_doc[1].rect
    out_doc.close()
