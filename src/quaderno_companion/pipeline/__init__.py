"""Pipeline modules for document fetching, optimization, and template generation."""

from quaderno_companion.pipeline.fetcher import ContentFetcher, FetchedDocument
from quaderno_companion.pipeline.optimizer import EinkOptimizer, optimize_pdf_for_eink
from quaderno_companion.pipeline.templates import EinkDocumentBuilder
from quaderno_companion.pipeline.transcriber import (
    find_annotated_pages,
    render_page_to_png,
    transcribe_image_with_gemini,
    transcribe_pdf,
)

__all__ = [
    "ContentFetcher",
    "FetchedDocument",
    "EinkOptimizer",
    "optimize_pdf_for_eink",
    "EinkDocumentBuilder",
    "find_annotated_pages",
    "render_page_to_png",
    "transcribe_image_with_gemini",
    "transcribe_pdf",
]

