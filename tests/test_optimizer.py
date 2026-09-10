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


def test_textbook_with_cover_page_margin_cropping():
    """Verify that a full-bleed/large cover page does not prevent margin trimming on interior pages."""
    doc = fitz.open()

    # Page 0: Textbook Cover (Title near top, artwork in center, publisher at bottom)
    cover = doc.new_page(width=595, height=842)
    cover.insert_textbox(fitz.Rect(50, 40, 545, 120), "ADVANCED QUANTUM MECHANICS", fontsize=24)
    cover.draw_rect(fitz.Rect(50, 150, 545, 700), fill=(0.8, 0.8, 0.8))
    cover.insert_textbox(fitz.Rect(50, 750, 545, 800), "Academic Press 2026", fontsize=14)

    # Pages 1 to 4: Interior textbook pages with wide 1.5-inch margins (content ~320x450 pt)
    for i in range(1, 5):
        page = doc.new_page(width=595, height=842)
        # Content centered with large margins
        page.insert_textbox(
            fitz.Rect(140, 180, 460, 630),
            f"Chapter {i}: Schrödinger Equation Applications\n\n"
            "This is standard body reading text formatted with wide academic margins. "
            "On an E-ink screen, these wide margins should be cropped out so that the font size "
            "is enlarged and comfortable to read.",
            fontsize=12,
        )

    src_bytes = doc.tobytes()
    doc.close()

    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes = optimizer.optimize_pdf(src_bytes, trim_margins=True)

    out_doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(out_doc) == 5

    # Page 0 (Cover) content should be preserved
    cover_text = out_doc[0].get_text()
    assert "ADVANCED QUANTUM MECHANICS" in cover_text
    assert "Academic Press 2026" in cover_text

    # Verify interior pages (1-4) had margins cropped:
    # In source document, text bounding box width was ~320 pt on a 595 pt page (~53% width).
    # After margin trimming, the content should occupy >= 80% of the target page width!
    p1_blocks = out_doc[1].get_text("blocks")
    assert len(p1_blocks) > 0
    p1_text_rect = fitz.Rect(p1_blocks[0][:4])
    for b in p1_blocks[1:]:
        p1_text_rect |= fitz.Rect(b[:4])

    # Text box width on the cropped page should be significantly wider than in uncropped page (>= 480 pt)
    assert p1_text_rect.width >= 480.0, f"Expected cropped text width >= 480, got {p1_text_rect.width}"

    # Verify visual scale consistency across interior pages 1 and 2
    p2_blocks = out_doc[2].get_text("blocks")
    p2_text_rect = fitz.Rect(p2_blocks[0][:4])
    for b in p2_blocks[1:]:
        p2_text_rect |= fitz.Rect(b[:4])
    assert abs(p1_text_rect.width - p2_text_rect.width) < 1.0

    out_doc.close()


def test_textbook_with_back_cover_outlier():
    """Verify that a back cover outlier does not disrupt interior reading page margin trimming."""
    doc = fitz.open()

    # Page 0: Cover
    p0 = doc.new_page(width=595, height=842)
    p0.draw_rect(fitz.Rect(30, 30, 565, 810), fill=(0.7, 0.7, 0.7))

    # Pages 1..3: Interior pages with wide margins
    for i in range(1, 4):
        p = doc.new_page(width=595, height=842)
        p.insert_textbox(fitz.Rect(150, 200, 450, 600), f"Body text page {i}", fontsize=12)

    # Page 4: Back Cover (Full bleed blurb and barcode)
    p4 = doc.new_page(width=595, height=842)
    p4.draw_rect(fitz.Rect(20, 20, 575, 820), fill=(0.6, 0.6, 0.6))

    src_bytes = doc.tobytes()
    doc.close()

    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes = optimizer.optimize_pdf(src_bytes, trim_margins=True)

    out_doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(out_doc) == 5

    # Check that interior page 1 is properly scaled up despite front & back covers
    p1_orig_blocks = fitz.open(stream=src_bytes, filetype="pdf")[1].get_text("blocks")
    p1_orig_width = p1_orig_blocks[0][2] - p1_orig_blocks[0][0]

    p1_blocks = out_doc[1].get_text("blocks")
    assert len(p1_blocks) > 0
    p1_cropped_width = p1_blocks[0][2] - p1_blocks[0][0]

    # Margins should be cropped, magnifying the text substantially (scale factor >= 2.0x)
    scale_factor = p1_cropped_width / p1_orig_width
    assert scale_factor >= 2.0, f"Expected scale factor >= 2.0, got {scale_factor}"

    out_doc.close()


def test_two_page_document_without_cover():
    """Verify that a standard 2-page document with normal margins maintains uniform scale across both pages."""
    doc = fitz.open()

    # Page 1: Normal letter/article page
    p1 = doc.new_page(width=595, height=842)
    p1.insert_textbox(fitz.Rect(100, 100, 495, 700), "Letter Page 1 with regular margins.", fontsize=12)

    # Page 2: Short conclusion
    p2 = doc.new_page(width=595, height=842)
    p2.insert_textbox(fitz.Rect(100, 100, 495, 300), "Letter Page 2 with short ending note.", fontsize=12)

    src_bytes = doc.tobytes()
    doc.close()

    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes = optimizer.optimize_pdf(src_bytes, trim_margins=True)

    out_doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(out_doc) == 2

    # Compute magnification scale factor on both pages
    src_doc = fitz.open(stream=src_bytes, filetype="pdf")
    orig_b1 = src_doc[0].get_text("blocks")[0]
    orig_b2 = src_doc[1].get_text("blocks")[0]
    orig_w1 = orig_b1[2] - orig_b1[0]
    orig_w2 = orig_b2[2] - orig_b2[0]

    crop_b1 = out_doc[0].get_text("blocks")[0]
    crop_b2 = out_doc[1].get_text("blocks")[0]
    crop_w1 = crop_b1[2] - crop_b1[0]
    crop_w2 = crop_b2[2] - crop_b2[0]

    scale1 = crop_w1 / orig_w1
    scale2 = crop_w2 / orig_w2

    # Both pages should share the exact same scale factor
    assert abs(scale1 - scale2) < 0.01, f"Expected matching scales, got {scale1} and {scale2}"

    out_doc.close()
    src_doc.close()


def test_math_equation_transformation_matrix_preservation():
    """Verify that optimization preserves content streams without corruption from stream cleaning.
    
    Checks that mathematical equations with transformation matrices and graphics states
    are preserved without overlapping text elements.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    
    # Simulate a document with paragraph text and a formula with nested transformation state
    page.insert_textbox(fitz.Rect(50, 100, 545, 150), "Paragraph before mathematical formulation.", fontsize=12)
    page.insert_textbox(fitz.Rect(100, 200, 300, 250), "R(q) = [x, y, z]", fontsize=14)
    page.insert_textbox(fitz.Rect(50, 300, 545, 350), "Paragraph following mathematical formulation.", fontsize=12)
    
    src_bytes = doc.tobytes()
    doc.close()
    
    optimizer = EinkOptimizer(profile_name="A4")
    out_bytes = optimizer.optimize_pdf(src_bytes, trim_margins=True)
    
    out_doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(out_doc) == 1
    
    out_page = out_doc[0]
    blocks = out_page.get_text("blocks")
    # Verify we still have the distinct text blocks without vertical collapse
    assert len(blocks) >= 3
    y_coords = [b[1] for b in blocks]
    # Y-coordinates should remain strictly monotonic (top to bottom)
    assert y_coords == sorted(y_coords), "Text blocks collapsed or overlapped after optimization!"
    out_doc.close()
