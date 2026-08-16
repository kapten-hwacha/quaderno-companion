"""FastAPI Local Daemon and Webhooks for Quaderno Companion.

Exposes REST endpoints for:
- POST /api/documents/open (pushes PDF blob or local path/URL and opens it)
- POST /api/viewer/page (navigates to absolute or relative page index)
- GET /api/viewer/status (fetches active document ID, title, and page index)
- GET /api/device/status (fetches battery, storage, route, reading state)
- POST /api/agent/push (one-click push endpoint for bookmarklets & browser extensions)
- POST /api/agent/chat (agent natural language intent execution)
- GET / (Embedded dashboard for device status and control)
"""

import collections
from contextlib import asynccontextmanager
import io
import logging
import threading
import time
from typing import Any, Dict, Literal, Optional
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from quaderno_companion.agent.core import agent
from quaderno_companion.agent.tools import (
    NavigateReaderParams,
    PushDocumentParams,
    tool_get_reading_state,
    tool_navigate_reader,
    tool_push_document,
    tool_summarize_to_eink,
)
from quaderno_companion.config import settings
from quaderno_companion.device.manager import device_manager
from quaderno_companion.fs.syncer import sync_runner, syncer
from quaderno_companion.pipeline.optimizer import EinkOptimizer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan managing directory initialization, background sync, and cache cleanup."""
    settings.ensure_directories()
    deleted = settings.clean_cache(max_age_days=7, max_total_mb=200)
    if deleted > 0:
        logger.info(f"Cleaned {deleted} stale cache file(s) on startup.")
    
    if settings.auto_sync_enabled:
        sync_runner.start()
    try:
        yield
    finally:
        sync_runner.stop()


app = FastAPI(
    title="Quaderno Companion Daemon",
    description="Autonomous E-Ink bridge and reader navigation controller for Fujitsu Quaderno Gen 2",
    version="0.1.0",
    lifespan=lifespan,
)

# Maximum allowed file upload size (100 MB)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Enable CORS for browser extensions and local bookmarklets
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------- Security & Rate Limiting ----------------

class SlidingWindowRateLimiter:
    """Sliding-window in-memory rate limiter per client IP."""

    def __init__(self, requests_per_minute: int = 120):
        self.rate = requests_per_minute
        self.window = 60.0
        self._history: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._history[client_ip]
            while q and (now - q[0]) > self.window:
                q.popleft()
            if len(q) >= self.rate:
                return False
            q.append(now)
            return True


rate_limiter = SlidingWindowRateLimiter(requests_per_minute=120)


def verify_rate_limit(request: Request):
    """Enforce rate limits per client IP."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": "60"},
        )


def verify_api_auth(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Verify daemon authentication when QUADERNO_API_KEY is configured."""
    if not settings.api_key:
        return True  # Auth is disabled by default for zero-friction local bridge

    # Check X-API-Key header
    if x_api_key and x_api_key.strip() == settings.api_key.strip():
        return True

    # Check Authorization: Bearer <key>
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == settings.api_key.strip():
            return True
        if authorization.strip() == settings.api_key.strip():
            return True

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Invalid or missing API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------- Request Models ----------------

class OpenDocumentRequest(BaseModel):
    url_or_path: Optional[str] = Field(None, description="URL or local path to PDF/article.")
    title: Optional[str] = Field(None, description="Document display title.")
    page: int = Field(1, description="Page number to open (1-indexed).")
    profile: Optional[str] = Field(None, description="Target profile ('A4' or 'A5').")


class PageNavigationRequest(BaseModel):
    action: Literal["next", "prev", "goto", "offset"] = Field(..., description="'next', 'prev', 'goto', or 'offset'")
    page: Optional[int] = Field(None, description="Target page number or offset delta.")


class AgentPushRequest(BaseModel):
    url: str = Field(..., description="Web page URL or PDF link to push.")
    title: Optional[str] = Field(None, description="Page title.")
    summarize: bool = Field(False, description="Whether to summarize before pushing.")
    pages: Optional[int] = Field(None, description="Target summary length in pages (1–5).")
    notebook_url: Optional[str] = Field(None, description="Gemini Notebook URL.")
    notebook_id: Optional[str] = Field(None, description="Gemini Notebook ID from library.")
    provider: Optional[str] = Field(None, description="Summarizer provider ('gemini_notebook', 'gemini_api', 'rule_based', 'auto').")
    notebook_mode: Optional[str] = Field(None, description="Notebook mode ('ephemeral' fresh notebook, 'shared' existing).")
    cleanup: Optional[bool] = Field(None, description="Whether to auto-delete ephemeral notebook after summary.")


class AgentChatRequest(BaseModel):
    query: str = Field(..., description="Natural language command or query.")


# ---------------- API Endpoints ----------------

@app.get("/healthz")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "app": "quaderno-companion"}


@app.get(
    "/api/device/status",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def get_device_status():
    """Fetch complete Quaderno connection, battery, storage, and reading status."""
    try:
        status = await device_manager.get_status()
        return {
            "is_connected": status.is_connected,
            "is_paired": status.is_paired,
            "connection_type": status.connection_type,
            "host": status.host,
            "port": status.port,
            "battery_level": status.battery_level,
            "battery_charging": status.battery_charging,
            "storage_total_mb": status.storage_total_mb,
            "storage_free_mb": status.storage_free_mb,
            "reading_state": {
                "document_id": status.reading_state.document_id,
                "title": status.reading_state.title,
                "remote_path": status.reading_state.remote_path,
                "current_page": status.reading_state.current_page,
                "total_pages": status.reading_state.total_pages,
                "last_updated": status.reading_state.last_updated.isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Error fetching device status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch device status.")


@app.get(
    "/api/viewer/status",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def get_viewer_status():
    """Fetch active reading document title, ID, and page position."""
    try:
        return await tool_get_reading_state()
    except Exception as e:
        logger.error(f"Error fetching viewer status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch viewer status.")


@app.post(
    "/api/documents/open",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def open_document(
    request: Request,
    payload: Optional[OpenDocumentRequest] = None,
    file: Optional[UploadFile] = File(None),
    title: Optional[str] = Form(None),
    page: int = Form(1),
):
    """Pushes PDF blob or local path/URL and opens it on Quaderno."""
    try:
        content_type = request.headers.get("content-type", "").lower()

        # Case 1: JSON payload
        if "application/json" in content_type:
            body = await request.json()
            payload = OpenDocumentRequest(**body)
            if payload and payload.url_or_path:
                result = await tool_push_document(
                    source_url_or_path=payload.url_or_path,
                    title=payload.title,
                    page=payload.page,
                    profile=payload.profile,
                )
                return result

        # Case 2: Direct multipart file upload
        if file is not None:
            raw_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(raw_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large (exceeds 100MB limit).")
            doc_title = title or file.filename or "Uploaded Document"
            optimizer = EinkOptimizer(profile_name=settings.default_profile)
            opt_pdf = optimizer.optimize_pdf(raw_bytes, trim_margins=True)

            result = await device_manager.open_document(
                pdf_bytes=opt_pdf,
                filename=file.filename or "document.pdf",
                title=doc_title,
                page=page,
            )
            return {"status": "success", "result": result}

        # Case 3: Form url_or_path
        if payload and payload.url_or_path:
            result = await tool_push_document(
                source_url_or_path=payload.url_or_path,
                title=payload.title,
                page=payload.page,
                profile=payload.profile,
            )
            return result

        raise HTTPException(status_code=400, detail="Must provide either a file upload or 'url_or_path' in payload.")
    except HTTPException:
        raise
    except ValueError as ve:
        logger.warning(f"Validation error opening document: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error opening document: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to open document on Quaderno.")


@app.post(
    "/api/viewer/page",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def navigate_page(nav: PageNavigationRequest):
    """Navigates Quaderno viewer (next / prev / goto / offset)."""
    try:
        return await tool_navigate_reader(action=nav.action, page=nav.page)
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Navigation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Navigation command failed.")


@app.post(
    "/api/agent/push",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def agent_push(req: AgentPushRequest):
    """Zero-friction trigger endpoint for bookmarklets and browser extensions."""
    try:
        if req.summarize or (req.pages is not None and req.pages > 0):
            target_pages = req.pages or 1
            return await agent.summarize_and_push(
                req.url,
                title=req.title,
                pages=target_pages,
                notebook_url=req.notebook_url,
                notebook_id=req.notebook_id,
                provider=req.provider,
                notebook_mode=req.notebook_mode,
                cleanup=req.cleanup,
            )
        else:
            return await tool_push_document(source_url_or_path=req.url, title=req.title)
    except HTTPException:
        raise
    except ValueError as ve:
        logger.warning(f"Agent push validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Agent push error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent push processing failed.")


@app.post(
    "/api/agent/chat",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def agent_chat(req: AgentChatRequest):
    """Natural language agent command dispatch."""
    try:
        return await agent.execute_instruction(req.query)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent execution failed.")


@app.post(
    "/api/sync",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def trigger_sync():
    """Triggers an immediate bidirectional sync pass between Quaderno and local folder."""
    try:
        res = syncer.sync_pass()
        return res.to_dict()
    except Exception as e:
        logger.error(f"Sync pass failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}")


@app.get(
    "/api/sync/status",
    dependencies=[Depends(verify_rate_limit), Depends(verify_api_auth)],
)
async def sync_status():
    """Returns status of sync engine and last sync pass."""
    last_res = sync_runner.last_result.to_dict() if sync_runner.last_result else None
    return {
        "auto_sync_enabled": settings.auto_sync_enabled,
        "is_running": sync_runner.is_running,
        "sync_dir": str(settings.sync_dir),
        "last_sync": last_res,
    }


@app.get("/")
async def root():
    """API daemon status and health."""
    return {
        "status": "ok",
        "app": "quaderno-companion",
        "version": "0.1.0",
    }
