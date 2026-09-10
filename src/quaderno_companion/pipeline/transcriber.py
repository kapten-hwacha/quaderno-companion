"""Handwritten Notes & Mathematical Formula OCR Transcriber.

Extracts handwritten ink annotations and page content from Quaderno PDFs
and transcribes them into GitHub-Flavored Markdown with LaTeX math using the
Google Gemini Flash-Lite multimodal API.
"""

import base64
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
import pymupdf

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)

# Prompt optimized for handwriting and mathematical notation recognition
DEFAULT_OCR_PROMPT = """You are an expert transcription assistant specializing in transcribing handwritten notes, diagrams, and mathematical expressions into digital text.

Carefully examine this image containing handwritten notes from an E-ink digital paper device (Fujitsu Quaderno).

Transcribe the content into clean, well-formatted GitHub-flavored Markdown following these strict instructions:

1. MATHEMATICAL FORMULAS & NOTATION (HIGHEST PRIORITY):
   - Convert all mathematical formulas, symbols, fractions, matrices, integrals, and equations into standard LaTeX.
   - Use `$ ... $` for inline math expressions and single variables.
   - Use `$$ ... $$` on dedicated lines for standalone equations, multi-line derivations, and theorems.
   - Disambiguate contextually similar characters (e.g., $x$ vs \\times, $v$ vs \\nu, $1$ vs $l$, $t$ vs $+$, $\\in$ vs $E$, $\\theta$ vs $0$).

2. STRUCTURE & CONTENT:
   - Preserve headings, bullet points, numbered lists, and horizontal dividers.
   - If diagrams, charts, or sketches are present, provide a clear descriptive text summary or a Mermaid diagram if applicable.
   - If the page contains both printed document text and handwritten notes/marginalia, clearly highlight the handwritten annotations (e.g., under a '### Handwritten Notes & Annotations' section).
   - If handwriting is crossed out or scratched over, omit or note it as [struck through].

3. OUTPUT FORMAT:
   - Output ONLY the transcribed Markdown content.
   - Do NOT include conversational preambles, greetings, or conclusions (e.g., do not say "Here is your transcription:").
"""


def find_annotated_pages(doc_or_path: Union[str, Path, bytes, pymupdf.Document]) -> List[int]:
    """Scan a PDF and return a list of 1-indexed page numbers containing annotations (ink, highlights, text).
    
    Ink annotations from the Quaderno stylus have type `pymupdf.PDF_ANNOT_INK`.
    """
    should_close = False
    if isinstance(doc_or_path, pymupdf.Document):
        doc = doc_or_path
    elif isinstance(doc_or_path, (str, Path)):
        doc = pymupdf.open(str(doc_or_path))
        should_close = True
    elif isinstance(doc_or_path, bytes):
        doc = pymupdf.open(stream=doc_or_path, filetype="pdf")
        should_close = True
    else:
        raise TypeError(f"Unsupported document type: {type(doc_or_path)}")

    annotated_pages: List[int] = []
    try:
        for page_idx, page in enumerate(doc):
            annots = list(page.annots())
            if annots:
                annotated_pages.append(page_idx + 1)
    finally:
        if should_close:
            doc.close()

    return annotated_pages


def render_page_to_png(page: pymupdf.Page, dpi: int = 200) -> bytes:
    """Render a PDF page to high-quality PNG bytes."""
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes(output="png")


async def transcribe_image_with_gemini(
    image_bytes: bytes,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an image to the Gemini multimodal API for transcription."""
    resolved_key = api_key or settings.resolve_gemini_api_key()
    if not resolved_key:
        raise ValueError(
            "Gemini API key not found. Please set QUADERNO_GEMINI_API_KEY or GEMINI_API_KEY in your environment, "
            "or add it to ~/.config/quaderno/.env"
        )

    target_model = model or settings.gemini_model or "gemini-2.5-flash-lite"
    selected_prompt = prompt or DEFAULT_OCR_PROMPT

    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": selected_prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64_image,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    models_to_try = [target_model]
    if target_model != "gemini-2.0-flash-lite":
        models_to_try.append("gemini-2.0-flash-lite")
    if "gemini-1.5-flash" not in models_to_try:
        models_to_try.append("gemini-1.5-flash")

    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        for m in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
            try:
                resp = await client.post(
                    url,
                    params={"key": resolved_key},
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 404:
                    logger.warning(f"Model '{m}' returned 404, attempting fallback...")
                    continue
                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    return {"text": "", "model": m, "usage": data.get("usageMetadata", {})}

                content_parts = candidates[0].get("content", {}).get("parts", [])
                text_result = "".join(part.get("text", "") for part in content_parts)
                return {
                    "text": text_result.strip(),
                    "model": m,
                    "usage": data.get("usageMetadata", {}),
                }
            except httpx.HTTPStatusError as e:
                last_error = e
                # If model not found or unsupported, try next model
                if e.response.status_code in (400, 404):
                    continue
                raise
            except Exception as e:
                last_error = e
                raise

    if last_error:
        raise last_error
    raise RuntimeError("Failed to generate transcription with available Gemini models.")


async def transcribe_pdf(
    pdf_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    pages: Optional[List[int]] = None,
    only_annotated: bool = True,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    dpi: int = 200,
) -> Dict[str, Any]:
    """Transcribe handwritten notes and math from a PDF file into a Markdown document.
    
    Args:
        pdf_path: Path to the PDF file.
        output_path: Destination path for the .md file (defaults to alongside PDF).
        pages: Specific list of 1-indexed pages to transcribe.
        only_annotated: If True and pages is not provided, only transcribes pages with annotations.
        model: Gemini model name override.
        api_key: Gemini API key override.
        dpi: DPI for rendering page images (default 200).
        
    Returns:
        Dict containing transcription metadata, output file path, and Markdown text.
    """
    src_file = Path(pdf_path)
    if not src_file.exists():
        raise FileNotFoundError(f"PDF file not found: {src_file}")

    resolved_key = api_key or settings.resolve_gemini_api_key()
    if not resolved_key:
        raise ValueError(
            "Gemini API key not found. Please set QUADERNO_GEMINI_API_KEY or GEMINI_API_KEY in your environment, "
            "or add it to ~/.config/quaderno/.env"
        )

    doc = pymupdf.open(str(src_file))
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError(f"PDF file has 0 pages: {src_file}")

    try:
        # Determine target pages (1-indexed)
        if pages:
            target_pages = [p for p in pages if 1 <= p <= total_pages]
        elif only_annotated:
            annotated = find_annotated_pages(doc)
            # If no annotations found, default to all pages so user still gets a transcription
            target_pages = annotated if annotated else list(range(1, total_pages + 1))
        else:
            target_pages = list(range(1, total_pages + 1))

        target_pages = sorted(list(set(target_pages)))
        if not target_pages:
            target_pages = [1]

        logger.info(f"Transcribing {len(target_pages)} page(s) from '{src_file.name}': {target_pages}")

        transcriptions: List[Dict[str, Any]] = []
        used_model = model or settings.gemini_model

        for page_num in target_pages:
            page = doc[page_num - 1]
            img_bytes = render_page_to_png(page, dpi=dpi)
            res = await transcribe_image_with_gemini(
                image_bytes=img_bytes,
                api_key=resolved_key,
                model=model,
            )
            used_model = res.get("model", used_model)
            transcriptions.append({
                "page": page_num,
                "text": res.get("text", ""),
                "usage": res.get("usage", {}),
            })
    finally:
        doc.close()

    # Determine output file path
    if output_path:
        out_file = Path(output_path)
    else:
        out_file = src_file.parent / f"{src_file.stem}_transcribed.md"

    # Assemble structured markdown document
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc_title = src_file.stem.replace("_", " ").title()

    lines = [
        f"# {doc_title} - Transcribed Notes",
        "",
        f"> **Source File**: `{src_file.name}`  ",
        f"> **Transcribed**: {now_str}  ",
        f"> **Model**: `{used_model}`  ",
        f"> **Pages**: {', '.join(str(p) for p in target_pages)} of {total_pages}  ",
        "",
        "---",
        "",
    ]

    for item in transcriptions:
        lines.append(f"## Page {item['page']}")
        lines.append("")
        lines.append(item["text"])
        lines.append("")
        lines.append("---")
        lines.append("")

    full_markdown = "\n".join(lines).strip() + "\n"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(full_markdown, encoding="utf-8")
    logger.info(f"Transcribed markdown written to '{out_file}'")

    return {
        "status": "success",
        "source_file": str(src_file),
        "output_file": str(out_file),
        "pages": target_pages,
        "total_pages": total_pages,
        "model": used_model,
        "content": full_markdown,
    }
