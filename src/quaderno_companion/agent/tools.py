"""Standardized Tool Definitions for Quaderno Agent.

Exposes atomic, E-ink specific operations for LLM function calling and intent dispatch:
1. push_document: Ingests, optimizes, uploads, and displays a document.
2. navigate_reader: Navigates reading pages on active document.
3. summarize_to_eink: Synthesizes text/URL into structured 1-page E-ink brief and pushes to display.
4. get_reading_state: Queries active document title, ID, and page position.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, TypedDict, Union

from pydantic import BaseModel, Field

from quaderno_companion.config import settings
from quaderno_companion.device.manager import (
    NavAction,
    NavigateResult,
    OpenDocumentResult,
    device_manager,
)
from quaderno_companion.pipeline.fetcher import ContentFetcher
from quaderno_companion.pipeline.optimizer import EinkOptimizer
from quaderno_companion.pipeline.templates import EinkDocumentBuilder

logger = logging.getLogger(__name__)


# ---------------- Tool Return Types ----------------

class PushDocumentResult(TypedDict):
    """Return shape for tool_push_document."""
    status: str
    message: str
    details: OpenDocumentResult


class NavigateToolResult(TypedDict):
    """Return shape for tool_navigate_reader."""
    status: str
    message: str
    details: NavigateResult


class SummarizeToolResult(TypedDict):
    """Return shape for tool_summarize_to_eink."""
    status: str
    message: str
    details: OpenDocumentResult


# General union alias for tool envelope
ToolResult = Union[PushDocumentResult, NavigateToolResult, SummarizeToolResult]


class ReadingStateResult(TypedDict):
    """Return shape for tool_get_reading_state."""
    status: str
    is_connected: bool
    is_paired: bool
    connection_type: str
    battery_level: Optional[int]
    battery_charging: Optional[bool]
    active_document: Dict[str, Any]


# ---------------- Tool Parameter Models ----------------

class PushDocumentParams(BaseModel):
    source_url_or_path: str = Field(
        ...,
        description="HTTP/HTTPS URL, ArXiv link, or local file path (.pdf, .md, .txt, .html).",
    )
    title: Optional[str] = Field(
        default=None,
        description="Optional display title for the document.",
    )
    page: int = Field(
        default=1,
        description="Initial page number to open on the device (default: 1).",
    )
    profile: Optional[str] = Field(
        default=None,
        description="Target screen profile ('A4' or 'A5'). Defaults to settings.",
    )


class NavigateReaderParams(BaseModel):
    action: NavAction = Field(
        ...,
        description="Navigation direction: 'next' (next page), 'prev' (previous page), 'goto' (jump to page number), 'offset' (relative jump +/- n).",
    )
    page: Optional[int] = Field(
        default=None,
        description="Target page number (for 'goto') or delta (for 'offset').",
    )


class SummarizeToEinkParams(BaseModel):
    text_or_url: str = Field(
        ...,
        description="Text content, markdown, or URL to summarize.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Title of the executive summary.",
    )
    key_takeaways: Optional[List[str]] = Field(
        default=None,
        description="Pre-computed bullet-point takeaways (3–5 items). If omitted, rule-based extraction is used.",
    )
    sections: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Dict mapping section heading → body text. If omitted, a single Overview section is generated.",
    )
    pages: Optional[int] = Field(
        default=1,
        description="Target page length of the summary (1–5 pages).",
    )


# ---------------- Tool Implementations ----------------

async def tool_push_document(
    source_url_or_path: str,
    title: Optional[str] = None,
    page: int = 1,
    profile: Optional[str] = None,
) -> PushDocumentResult:
    """Downloads, optimizes, uploads, and switches the Quaderno screen to the document."""
    target_profile = profile or settings.default_profile
    fetcher = ContentFetcher(profile_name=target_profile)

    doc = await fetcher.fetch(
        source_url_or_path=source_url_or_path,
        custom_title=title,
        optimize_for_eink=True,
    )

    result = await device_manager.open_document(
        pdf_bytes=doc.pdf_bytes,
        filename=doc.filename,
        title=doc.title,
        page=page,
    )

    from quaderno_companion.state import record_pushed_document
    if result.get("document_id"):
        record_pushed_document(
            doc_id=result["document_id"],
            title=doc.title,
            path=source_url_or_path,
        )

    return {
        "status": "success",
        "message": f"Opened '{doc.title}' on Quaderno (Page {result['page']}/{result['total_pages']})",
        "details": result,
    }


async def tool_navigate_reader(
    action: NavAction,
    page: Optional[int] = None,
) -> NavigateToolResult:
    """Changes page on the currently open document without triggering a re-upload."""
    result = await device_manager.navigate(action=action, page=page)
    return {
        "status": "success",
        "message": f"Navigated to page {result['page']}/{result['total_pages']}",
        "details": result,
    }


async def tool_get_reading_state() -> ReadingStateResult:
    """Queries active document metadata, page position, and device connection status."""
    status = await device_manager.get_status()
    return {
        "status": "success",
        "is_connected": status.is_connected,
        "is_paired": status.is_paired,
        "connection_type": status.connection_type,
        "battery_level": status.battery_level,
        "battery_charging": status.battery_charging,
        "active_document": {
            "title": status.reading_state.title,
            "document_id": status.reading_state.document_id,
            "current_page": status.reading_state.current_page,
            "total_pages": status.reading_state.total_pages,
            "last_updated": status.reading_state.last_updated.isoformat(),
        },
    }


async def tool_summarize_to_eink(
    text_or_url: str,
    title: Optional[str] = None,
    key_takeaways: Optional[List[str]] = None,
    sections: Optional[Dict[str, Any]] = None,
    pages: int = 1,
) -> SummarizeToolResult:
    """Generates a structured E-ink summary PDF and pushes it to the display."""
    doc_title = title or "Executive Brief"
    builder = EinkDocumentBuilder(profile_name=settings.default_profile)

    # If takeaways and sections are not provided, synthesize default structure
    takeaways = key_takeaways or [
        "Key insight extracted from source document.",
        "Synthesized for high-contrast E-ink reading ergonomics.",
    ]
    sec = sections or {
        "Overview": text_or_url if len(text_or_url) < 1500 else text_or_url[:1500] + "..."
    }

    pdf_bytes = builder.render_summary_pdf(
        title=doc_title,
        source_url=text_or_url if text_or_url.startswith("http") else None,
        key_takeaways=takeaways,
        sections=sec,
    )

    optimizer = EinkOptimizer(profile_name=settings.default_profile)
    optimized_pdf = optimizer.optimize_pdf(pdf_bytes, trim_margins=False)

    page_label = f"_{pages}p" if pages > 1 else ""
    filename = f"Summary_{int(time.time())}{page_label}_{doc_title[:30]}.pdf".replace(" ", "_")
    result = await device_manager.open_document(
        pdf_bytes=optimized_pdf,
        filename=filename,
        title=f"Summary: {doc_title}",
        page=1,
    )

    from quaderno_companion.state import record_pushed_document
    if result.get("document_id"):
        record_pushed_document(
            doc_id=result["document_id"],
            title=f"Summary: {doc_title}",
            path=text_or_url,
        )

    return {
        "status": "success",
        "message": f"Summary '{doc_title}' pushed to Quaderno display.",
        "details": result,
    }


# ---------------- Tool Registry & Schema ----------------

TOOL_DEFINITIONS = [
    {
        "name": "push_document",
        "description": "Downloads, compresses, uploads, and switches the Quaderno screen to the document.",
        "parameters": PushDocumentParams.model_json_schema(),
        "handler": tool_push_document,
    },
    {
        "name": "navigate_reader",
        "description": "Changes page on Quaderno reader (next / prev / goto / offset) without re-uploading.",
        "parameters": NavigateReaderParams.model_json_schema(),
        "handler": tool_navigate_reader,
    },
    {
        "name": "summarize_to_eink",
        "description": "Generates a structured 1-page summary, compiles it to PDF, and pushes it to the display.",
        "parameters": SummarizeToEinkParams.model_json_schema(),
        "handler": tool_summarize_to_eink,
    },
    {
        "name": "get_reading_state",
        "description": "Queries active document metadata, page position, and device status.",
        "parameters": {"type": "object", "properties": {}},
        "handler": tool_get_reading_state,
    },
]

TOOL_MAP: Dict[str, Any] = {
    "push_document": tool_push_document,
    "navigate_reader": tool_navigate_reader,
    "summarize_to_eink": tool_summarize_to_eink,
    "get_reading_state": tool_get_reading_state,
}
