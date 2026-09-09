"""Standardized Tool Definitions for Quaderno Companion.

Exposes atomic, E-ink specific operations for intent dispatch:
1. push_document: Ingests, optimizes, uploads, and displays a document.
2. navigate_reader: Navigates reading pages on active document.
3. get_reading_state: Queries active document title, ID, and page position.
"""

import json
import logging
import re
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


# General union alias for tool envelope
ToolResult = Union[PushDocumentResult, NavigateToolResult]


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
        description="HTTP/HTTPS URL, ArXiv link, or local file path (.pdf, .epub, .mobi, .md, .txt, .html).",
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
    destination_folder: Optional[str] = Field(
        default=None,
        description="Target destination folder on Quaderno storage (e.g. 'Document/Research').",
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


# ---------------- Tool Implementations ----------------

async def tool_push_document(
    source_url_or_path: str,
    title: Optional[str] = None,
    page: int = 1,
    profile: Optional[str] = None,
    destination_folder: Optional[str] = None,
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
        remote_folder=destination_folder,
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
        "name": "get_reading_state",
        "description": "Queries active document metadata, page position, and device status.",
        "parameters": {"type": "object", "properties": {}},
        "handler": tool_get_reading_state,
    },
]

TOOL_MAP: Dict[str, Any] = {
    "push_document": tool_push_document,
    "navigate_reader": tool_navigate_reader,
    "get_reading_state": tool_get_reading_state,
}
