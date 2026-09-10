"""E-Ink Preprocessing and Optimization Pipeline using PyMuPDF.

Optimizes PDFs for Fujitsu Quaderno Gen 2 (A4/A5) displays:
1. Whitespace / Margin Trimming: Expands content area by removing oversized margins.
2. Resolution & Aspect-Ratio Scaling: Scales to native E-ink dimensions (1650x2200 for A4, 1404x1872 for A5).
3. E-Ink Contrast Optimization & Dithering: High-contrast 8-bit grayscale or 1-bit Floyd-Steinberg dithering.
4. Stream Compression: Strips unnecessary metadata and compresses raster streams (< 300 KB payloads).
"""

import io
import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from quaderno_companion.config import SCREEN_PROFILES, ScreenProfile, settings

logger = logging.getLogger(__name__)


class EinkOptimizer:
    """PDF Optimizer tailored for Quaderno E-Ink screens."""

    def __init__(self, profile_name: Optional[str] = None):
        target = profile_name or settings.default_profile
        self.profile: ScreenProfile = SCREEN_PROFILES.get(target, SCREEN_PROFILES["A4"])

    def optimize_pdf(
        self,
        input_data: Union[str, Path, bytes],
        trim_margins: bool = True,
        dither_raster: bool = False,
        contrast_factor: float = 1.3,
        padding_pts: float = 18.0,  # ~0.25 inch reading margin
        output_path: Optional[Union[str, Path]] = None,
    ) -> bytes:
        """Optimize a PDF document for Quaderno E-ink display.

        Args:
            input_data: File path or raw bytes of the source PDF.
            trim_margins: Whether to crop excess page margins.
            dither_raster: Whether to apply Floyd-Steinberg 1-bit dithering to pages.
            contrast_factor: Contrast boost factor for grayscale images.
            padding_pts: Padding around content after margin trimming (points).
            output_path: Optional path to save the optimized PDF.

        Returns:
            Optimized PDF as bytes.
        """
        import pymupdf
        if dither_raster:
            from PIL import Image, ImageEnhance

        if isinstance(input_data, (str, Path)):
            doc = pymupdf.open(str(input_data))
        else:
            doc = pymupdf.open(stream=input_data, filetype="pdf")

        # Create target output document
        out_doc = pymupdf.open()

        # Target dimensions in standard ISO PDF points (72 points/inch: A4=595x842pt, A5=420x595pt)
        paper_code = "a5" if "A5" in self.profile.name else "a4"
        target_pt_w, target_pt_h = pymupdf.paper_size(paper_code)
        target_rect = pymupdf.Rect(0, 0, target_pt_w, target_pt_h)

        # Pass 1: Detect per-page content bounding boxes
        raw_page_data = []
        for page_idx in range(len(doc)):
            src_page = doc[page_idx]
            page_rect = src_page.rect
            is_landscape = page_rect.width > page_rect.height

            if trim_margins:
                raw_bbox = self._detect_content_bbox(src_page, padding_pts)
            else:
                raw_bbox = page_rect

            if not raw_bbox or raw_bbox.is_empty or raw_bbox.is_infinite:
                raw_bbox = page_rect

            raw_page_data.append((src_page, page_rect, is_landscape, raw_bbox))

        is_special_page = self._classify_special_pages(raw_page_data) if trim_margins else [False] * len(doc)

        # Collect body dimensions per orientation (excluding covers and full-bleed outliers)
        body_w_portrait: List[float] = []
        body_h_portrait: List[float] = []
        body_w_landscape: List[float] = []
        body_h_landscape: List[float] = []

        for idx, (_, page_rect, is_landscape, raw_bbox) in enumerate(raw_page_data):
            if not is_special_page[idx]:
                if is_landscape:
                    body_w_landscape.append(raw_bbox.width)
                    body_h_landscape.append(raw_bbox.height)
                else:
                    body_w_portrait.append(raw_bbox.width)
                    body_h_portrait.append(raw_bbox.height)

        def _get_uniform_dim(dims: List[float], fallback: float = 0.0) -> float:
            if not dims:
                return fallback
            if len(dims) >= 10:
                s = sorted(dims)
                idx = int(len(s) * 0.95)
                return s[min(idx, len(s) - 1)]
            return max(dims)

        fallback_w_portrait = max((d[3].width for d in raw_page_data if not d[2]), default=0.0)
        fallback_h_portrait = max((d[3].height for d in raw_page_data if not d[2]), default=0.0)
        fallback_w_landscape = max((d[3].width for d in raw_page_data if d[2]), default=0.0)
        fallback_h_landscape = max((d[3].height for d in raw_page_data if d[2]), default=0.0)

        uniform_w_portrait = _get_uniform_dim(body_w_portrait, fallback_w_portrait)
        uniform_h_portrait = _get_uniform_dim(body_h_portrait, fallback_h_portrait)
        uniform_w_landscape = _get_uniform_dim(body_w_landscape, fallback_w_landscape)
        uniform_h_landscape = _get_uniform_dim(body_h_landscape, fallback_h_landscape)

        # Pass 2: Position uniform-sized crop box around content per page to preserve constant scale
        for page_idx, (src_page, page_rect, is_landscape, raw_bbox) in enumerate(raw_page_data):
            is_special = is_special_page[page_idx]

            if trim_margins:
                page_w = target_pt_h if is_landscape else target_pt_w
                page_h = target_pt_w if is_landscape else target_pt_h

                if is_special:
                    # For cover or full-bleed outlier page:
                    # If page content is near full bleed (>= 95%), use page_rect so no edge art is trimmed
                    if raw_bbox.width >= page_rect.width * 0.95 and raw_bbox.height >= page_rect.height * 0.95:
                        content_rect = page_rect
                    else:
                        content_rect = raw_bbox
                else:
                    base_w = uniform_w_landscape if is_landscape else uniform_w_portrait
                    base_h = uniform_h_landscape if is_landscape else uniform_h_portrait

                    # Bound uniform dimensions to page boundaries
                    # And ensure content is never clipped if an individual page has wider content
                    crop_w = min(page_rect.width, max(base_w, raw_bbox.width))
                    crop_h = min(page_rect.height, max(base_h, raw_bbox.height))

                    # Center uniform box around this page's content, clamped within page bounds
                    cx = (raw_bbox.x0 + raw_bbox.x1) / 2.0
                    cy = (raw_bbox.y0 + raw_bbox.y1) / 2.0

                    x0 = max(page_rect.x0, min(page_rect.x1 - crop_w, cx - crop_w / 2.0))
                    y0 = max(page_rect.y0, min(page_rect.y1 - crop_h, cy - crop_h / 2.0))
                    x1 = min(page_rect.x1, x0 + crop_w)
                    y1 = min(page_rect.y1, y0 + crop_h)
                    content_rect = pymupdf.Rect(x0, y0, x1, y1)
            else:
                page_w = page_rect.width
                page_h = page_rect.height
                content_rect = page_rect

            if dither_raster:
                # Full rasterization + Floyd-Steinberg 1-bit dithering pipeline
                # Render at target native resolution
                target_pix_w = self.profile.height if is_landscape else self.profile.width
                target_pix_h = self.profile.width if is_landscape else self.profile.height
                zoom_x = target_pix_w / content_rect.width
                zoom_y = target_pix_h / content_rect.height
                zoom = min(zoom_x, zoom_y)
                mat = pymupdf.Matrix(zoom, zoom)

                # Render crop area to pixmap
                pix = src_page.get_pixmap(matrix=mat, clip=content_rect, colorspace=pymupdf.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)

                # Enhance contrast
                if contrast_factor != 1.0:
                    enhancer = ImageEnhance.Contrast(img)
                    img = enhancer.enhance(contrast_factor)

                # Convert to 1-bit with Floyd-Steinberg dithering
                dithered_img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

                # Convert back to PDF page
                img_byte_arr = io.BytesIO()
                dithered_img.save(img_byte_arr, format="PNG", optimize=True)
                img_bytes = img_byte_arr.getvalue()

                new_page = out_doc.new_page(width=page_w, height=page_h)
                
                # Center image in target page
                img_aspect = pix.width / pix.height
                page_aspect = page_w / page_h

                if img_aspect > page_aspect:
                    disp_w = page_w - (padding_pts * 2)
                    disp_h = disp_w / img_aspect
                else:
                    disp_h = page_h - (padding_pts * 2)
                    disp_w = disp_h * img_aspect

                offset_x = (page_w - disp_w) / 2.0
                offset_y = (page_h - disp_h) / 2.0
                dest_rect = pymupdf.Rect(offset_x, offset_y, offset_x + disp_w, offset_y + disp_h)

                new_page.insert_image(dest_rect, stream=img_bytes)
            else:
                # Vector-preserving transformation pipeline
                new_page = out_doc.new_page(width=page_w, height=page_h)

                # Compute scale to fit content in target_rect with padding
                avail_w = page_w - (padding_pts * 2)
                avail_h = page_h - (padding_pts * 2)

                scale_x = avail_w / content_rect.width
                scale_y = avail_h / content_rect.height
                scale = min(scale_x, scale_y)

                final_w = content_rect.width * scale
                final_h = content_rect.height * scale

                pos_x = (page_w - final_w) / 2.0
                pos_y = (page_h - final_h) / 2.0
                dest_rect = pymupdf.Rect(pos_x, pos_y, pos_x + final_w, pos_y + final_h)

                # Place cropped source page vector content into new page
                new_page.show_pdf_page(
                    dest_rect,
                    doc,
                    pno=page_idx,
                    clip=content_rect,
                )

        # Preserve original TOC / bookmark outlines in the optimized output document
        try:
            toc = doc.get_toc()
            if toc:
                out_doc.set_toc(toc)
        except Exception as e:
            logger.debug(f"Could not preserve TOC in optimized PDF: {e}")

        doc.close()

        # Compress output PDF streams for lightweight transmission.
        # NOTE: clean=False is critical. MuPDF's clean=True sanitization rewrites content streams
        # and improperly alters transformation matrices (Tm/cm) and graphics states (q/Q) in LaTeX
        # mathematical typesetting, causing equations, matrices, and text to collide and overlap.
        out_bytes = out_doc.tobytes(
            garbage=4,       # Remove unused objects
            clean=False,     # Do NOT rewrite content streams (preserves math equation matrices)
            deflate=True,    # Deflate uncompressed streams
            deflate_images=True,
            deflate_fonts=True,
        )
        out_doc.close()

        if output_path:
            Path(output_path).write_bytes(out_bytes)

        logger.info(
            f"Optimized PDF ({len(out_bytes)} bytes) for {self.profile.name} "
            f"({self.profile.width}x{self.profile.height})"
        )
        return out_bytes

    def optimize_file(
        self,
        file_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
    ) -> Tuple[bytes, str]:
        """Convert and optimize an arbitrary file (PDF, image, text, markdown) for Quaderno display.

        Returns:
            Tuple of (pdf_bytes, output_filename).
        """
        path = Path(file_path).expanduser().resolve()
        suffix = path.suffix.lower()
        title = path.stem

        if suffix == ".pdf":
            out_bytes = self.optimize_pdf(path, output_path=output_path)
            return out_bytes, f"{title}.pdf"

        elif suffix in (".epub", ".mobi"):
            import pymupdf
            from quaderno_companion.config import settings
            doc = pymupdf.open(str(path))
            meta_title = (doc.metadata or {}).get("title")
            if meta_title and meta_title.strip():
                title = meta_title.strip()
            paper_code = "a5" if "A5" in self.profile.name else "a4"
            target_pt_w, target_pt_h = pymupdf.paper_size(paper_code)
            if doc.is_reflowable:
                if hasattr(doc, "apply_css"):
                    doc.apply_css(f"* {{ font-family: {settings.normalized_font_family} !important; }}")
                doc.layout(width=target_pt_w, height=target_pt_h, fontsize=settings.ebook_font_size)
            pdf_bytes_tmp = doc.convert_to_pdf()
            doc.close()
            out_bytes = self.optimize_pdf(pdf_bytes_tmp, output_path=output_path)
            return out_bytes, f"{title}.pdf"

        elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
            import pymupdf
            img_doc = pymupdf.open(str(path))
            pdf_bytes_tmp = img_doc.convert_to_pdf()
            img_doc.close()
            out_bytes = self.optimize_pdf(pdf_bytes_tmp, output_path=output_path)
            return out_bytes, f"{title}.pdf"

        elif suffix in (".md", ".txt"):
            from quaderno_companion.pipeline.templates import EinkDocumentBuilder
            builder = EinkDocumentBuilder(profile_name=self.profile.name)
            content = path.read_text(encoding="utf-8")
            pdf_bytes = builder.render_article_pdf(
                title=title,
                content_html_or_text=content,
            )
            if output_path:
                Path(output_path).write_bytes(pdf_bytes)
            return pdf_bytes, f"{title}.pdf"

        elif suffix in (".html", ".htm"):
            from readability import Document
            from quaderno_companion.pipeline.templates import EinkDocumentBuilder
            builder = EinkDocumentBuilder(profile_name=self.profile.name)
            content = path.read_text(encoding="utf-8")
            doc = Document(content)
            title = doc.title() or title
            clean_html = doc.summary()
            pdf_bytes = builder.render_article_pdf(
                title=title,
                content_html_or_text=clean_html,
            )
            if output_path:
                Path(output_path).write_bytes(pdf_bytes)
            return pdf_bytes, f"{title}.pdf"

        else:
            raise ValueError(f"Unsupported file format for E-ink optimization: {suffix}")

    def _classify_special_pages(self, raw_page_data: List[Any]) -> List[bool]:
        """Identify cover pages, back covers, or full-bleed outlier pages.

        Excludes these pages from uniform body dimension calculations so that a
        full-page cover doesn't prevent margin trimming on interior textbook pages.
        """
        total_pages = len(raw_page_data)
        if total_pages <= 1:
            return [False] * total_pages

        is_special = [False] * total_pages

        area_ratios: List[float] = []
        w_ratios: List[float] = []
        h_ratios: List[float] = []
        for _, page_rect, _, raw_bbox in raw_page_data:
            pw = page_rect.width
            ph = page_rect.height
            if pw > 0 and ph > 0:
                w_r = raw_bbox.width / pw
                h_r = raw_bbox.height / ph
                a_r = (raw_bbox.width * raw_bbox.height) / (pw * ph)
            else:
                w_r = h_r = a_r = 1.0
            w_ratios.append(w_r)
            h_ratios.append(h_r)
            area_ratios.append(a_r)

        # Compute interior baseline (excluding page 0 and optionally last page)
        if total_pages >= 3:
            interior_indices = list(range(1, total_pages - 1))
        else:
            interior_indices = [1]

        interior_areas = [area_ratios[i] for i in interior_indices]
        interior_w = [w_ratios[i] for i in interior_indices]
        interior_h = [h_ratios[i] for i in interior_indices]

        sorted_areas = sorted(interior_areas)
        med_area = sorted_areas[len(sorted_areas) // 2]
        sorted_w = sorted(interior_w)
        med_w = sorted_w[len(sorted_w) // 2]
        sorted_h = sorted(interior_h)
        med_h = sorted_h[len(sorted_h) // 2]

        # 1. Front Cover (Page 0)
        # Check if Page 0 behaves like a cover:
        # - Has near full-bleed dimensions (w_ratio >= 0.88 and h_ratio >= 0.88) while interior has margins (med_w < 0.85 or med_h < 0.85)
        # - Or area is significantly larger than interior pages (area >= 0.70 and area >= med_area * 1.25)
        if (w_ratios[0] >= 0.88 and h_ratios[0] >= 0.88 and (med_w < 0.85 or med_h < 0.85)) or (
            area_ratios[0] >= 0.70 and area_ratios[0] >= med_area * 1.25
        ):
            is_special[0] = True

        # 2. Back Cover (Last page, if doc has >= 4 pages)
        if total_pages >= 4:
            last = total_pages - 1
            if (w_ratios[last] >= 0.88 and h_ratios[last] >= 0.88 and (med_w < 0.85 or med_h < 0.85)) or (
                area_ratios[last] >= 0.70 and area_ratios[last] >= med_area * 1.25
            ):
                is_special[last] = True

        # 3. Full-bleed outlier pages anywhere in the document
        for i in range(total_pages):
            if not is_special[i]:
                if w_ratios[i] >= 0.95 and h_ratios[i] >= 0.95 and med_area < 0.82:
                    is_special[i] = True

        # Safety: if all pages ended up classified as special, reset to False so normal uniform logic applies
        if all(is_special):
            is_special = [False] * total_pages

        return is_special

    def _detect_content_bbox(self, page: Any, margin_padding: float = 12.0) -> Any:
        """Find the bounding box of text, drawings, and images on a page."""
        import pymupdf
        page_rect = page.rect
        bbox = pymupdf.Rect()

        # 1. Fast text blocks detection
        try:
            for b in page.get_text("blocks"):
                rect = pymupdf.Rect(b[:4])
                if not rect.is_empty:
                    bbox |= rect
        except Exception:
            pass

        # 2. Fast C-level bounding box log for vector drawings and images
        try:
            for item in page.get_bboxlog():
                # item: (type, (x0, y0, x1, y1))
                r = pymupdf.Rect(item[1])
                if not r.is_empty:
                    if r.width >= page_rect.width * 0.96 and r.height >= page_rect.height * 0.96:
                        continue
                    bbox |= r
        except Exception:
            pass

        if bbox.is_empty:
            return page_rect

        # Expand bbox by safety margin, bounded by source page bounds
        expanded = pymupdf.Rect(
            max(page_rect.x0, bbox.x0 - margin_padding),
            max(page_rect.y0, bbox.y0 - margin_padding),
            min(page_rect.x1, bbox.x1 + margin_padding),
            min(page_rect.y1, bbox.y1 + margin_padding),
        )
        return expanded


def optimize_pdf_for_eink(
    input_data: Union[str, Path, bytes],
    profile: str = "A4",
    trim_margins: bool = True,
    dither: bool = False,
) -> bytes:
    """Convenience helper function to optimize a PDF."""
    optimizer = EinkOptimizer(profile_name=profile)
    return optimizer.optimize_pdf(input_data, trim_margins=trim_margins, dither_raster=dither)
