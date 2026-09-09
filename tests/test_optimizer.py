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


def test_optimizer_preserves_toc():
    """Verify that EinkOptimizer preserves document bookmarks and TOC structure."""
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((100, 100), "Chapter 1 Content")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((100, 100), "Chapter 2 Content")
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((100, 100), "Section 2.1 Content")

    toc_input = [
        [1, "Chapter 1: Intro", 1],
        [1, "Chapter 2: Methods", 2],
        [2, "Section 2.1: Details", 3],
    ]
    doc.set_toc(toc_input)
    src_bytes = doc.tobytes()
    doc.close()

    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes = optimizer.optimize_pdf(src_bytes, trim_margins=True)

    out_doc = fitz.open(stream=out_bytes, filetype="pdf")
    toc_output = out_doc.get_toc()
    out_doc.close()

    assert len(toc_output) == 3
    assert toc_output[0][:3] == [1, "Chapter 1: Intro", 1]
    assert toc_output[1][:3] == [1, "Chapter 2: Methods", 2]
    assert toc_output[2][:3] == [2, "Section 2.1: Details", 3]


def test_optimize_file_epub(tmp_path):
    """Verify EinkOptimizer.optimize_file converts and optimizes EPUB files."""
    import io
    import zipfile

    epub_path = tmp_path / "book.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        z.writestr("content.opf", """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB Optimization Test</dc:title></metadata>
  <manifest><item id="ch1" href="ch1.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="ch1"/></spine>
</package>""")
        z.writestr("ch1.html", "<html><body><h1>Optimization Test</h1><p>Sample e-book body.</p></body></html>")
    epub_path.write_bytes(buf.getvalue())

    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes, out_filename = optimizer.optimize_file(epub_path)

    assert len(out_bytes) > 0
    assert out_filename == "EPUB Optimization Test.pdf"

    # Verify output PDF dimensions match A4
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    assert abs(doc[0].rect.width - 595.0) < 1.0
    assert abs(doc[0].rect.height - 842.0) < 1.0
    doc.close()


def test_optimize_file_mobi(tmp_path):
    """Verify EinkOptimizer.optimize_file converts and optimizes MOBI files."""
    import struct

    mobi_path = tmp_path / "book.mobi"
    title = "MOBI Test"
    text = "Testing MOBI to Quaderno PDF conversion."
    name = (title[:31].encode("ascii", "ignore") + b"\0").ljust(32, b"\0")
    pdb_hdr = struct.pack(">32sHHIIIIII4s4sIIH", name, 0, 0, 0, 0, 0, 0, 0, 0, b"BOOK", b"MOBI", 0, 0, 2)
    rec0_offset = len(pdb_hdr) + 2 * 8 + 2
    rec1_offset = rec0_offset + 256
    rec_list = struct.pack(">IB3s", rec0_offset, 0, b"\0\0\0") + struct.pack(">IB3s", rec1_offset, 0, b"\0\0\1") + b"\0\0"
    palmdoc = struct.pack(">HHIHHI", 1, 0, len(text), 1, 4096, 0)
    mobi_hdr = struct.pack(">4sIII", b"MOBI", 232, 2, 65001) + (b"\0" * (232 - 16))
    rec0 = (palmdoc + mobi_hdr).ljust(256, b"\0")
    rec1 = text.encode("utf-8")
    mobi_path.write_bytes(pdb_hdr + rec_list + rec0 + rec1)

    optimizer = EinkOptimizer(profile_name="A5")
    out_bytes, out_filename = optimizer.optimize_file(mobi_path)

    assert len(out_bytes) > 0
    assert out_filename.endswith(".pdf")

    # Verify output PDF dimensions match A5 (420 x 595 pt)
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    assert abs(doc[0].rect.width - 420.0) < 1.0
    assert abs(doc[0].rect.height - 595.0) < 1.0
    doc.close()


def test_font_family_configuration(tmp_path):
    """Verify font_family switching between serif and sans-serif/non-serif."""
    import io
    import zipfile
    from quaderno_companion.config import settings

    epub_path = tmp_path / "font_test.epub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        z.writestr("content.opf", """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Font Test</dc:title></metadata>
  <manifest><item id="ch1" href="ch1.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="ch1"/></spine>
</package>""")
        z.writestr("ch1.html", "<html><body><p>Testing font families on e-ink.</p></body></html>")
    epub_path.write_bytes(buf.getvalue())

    orig = settings.font_family
    try:
        # 1. Non-serif / Sans-serif test
        settings.font_family = "non-serif"
        assert settings.normalized_font_family == "sans-serif"
        optimizer = EinkOptimizer(profile_name="A4")
        out_sans, _ = optimizer.optimize_file(epub_path)
        doc_sans = fitz.open(stream=out_sans, filetype="pdf")
        font_names_sans = [f[3] for f in doc_sans[0].get_fonts()]
        assert any("sans" in name.lower() for name in font_names_sans)
        doc_sans.close()

        # 2. Serif test
        settings.font_family = "serif"
        assert settings.normalized_font_family == "serif"
        out_serif, _ = optimizer.optimize_file(epub_path)
        doc_serif = fitz.open(stream=out_serif, filetype="pdf")
        font_names_serif = [f[3] for f in doc_serif[0].get_fonts()]
        assert any("charis" in name.lower() or "serif" in name.lower() or "times" in name.lower() for name in font_names_serif)
        doc_serif.close()
    finally:
        settings.font_family = orig



