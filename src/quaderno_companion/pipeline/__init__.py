"""Pipeline modules for document fetching, optimization, and template generation."""

from quaderno_companion.pipeline.fetcher import ContentFetcher, FetchedDocument
from quaderno_companion.pipeline.optimizer import EinkOptimizer, optimize_pdf_for_eink
from quaderno_companion.pipeline.templates import EinkDocumentBuilder

__all__ = [
    "ContentFetcher",
    "FetchedDocument",
    "EinkOptimizer",
    "optimize_pdf_for_eink",
    "EinkDocumentBuilder",
]
