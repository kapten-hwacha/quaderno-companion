"""High-Level Quaderno Device Controller and State Manager.

Coordinates auto-routing, document synchronization, reading state tracking,
and page navigation commands.
"""

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union

import pymupdf as fitz

from quaderno_companion.config import settings
from quaderno_companion.device.client import (
    DeviceNotConnectedError,
    DeviceNotPairedError,
    QuadernoClient,
)
from quaderno_companion.device.router import ConnectionType, DeviceRoute, NetworkRouter

logger = logging.getLogger(__name__)

NavAction = Literal["next", "prev", "goto", "offset"]


class OpenDocumentResult(TypedDict):
    """Shape returned by QuadernoDeviceManager.open_document()."""
    status: str
    document_id: str
    title: str
    remote_path: str
    page: int
    total_pages: int


class NavigateResult(TypedDict):
    """Shape returned by QuadernoDeviceManager.navigate()."""
    status: str
    document_id: str
    title: Optional[str]
    page: int
    total_pages: int
    action: str


@dataclass
class ReadingState:
    """Active document reading position and metadata."""
    document_id: Optional[str] = None
    title: Optional[str] = None
    remote_path: Optional[str] = None
    current_page: int = 1
    total_pages: int = 1
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class DeviceStatus:
    """Consolidated Quaderno device telemetry and reading context."""
    is_connected: bool = False
    is_paired: bool = False
    connection_type: ConnectionType = "unknown"
    host: Optional[str] = None
    port: int = 8443
    battery_level: Optional[int] = None
    battery_charging: Optional[bool] = None
    storage_total_mb: Optional[float] = None
    storage_free_mb: Optional[float] = None
    reading_state: ReadingState = field(default_factory=ReadingState)


def extract_pdf_toc(pdf_bytes: bytes) -> List[Tuple[str, int]]:
    """Extract Table of Contents bookmarks/outlines from PDF bytes."""
    if not pdf_bytes:
        return []
    
    # 1. Primary: Use PyMuPDF (fitz)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        toc = doc.get_toc()
        doc.close()
        if toc:
            entries = []
            for item in toc:
                if len(item) >= 3:
                    title = str(item[1]).strip()
                    page = int(item[2])
                    if title and page >= 1:
                        entries.append((title, page))
            if entries:
                return entries
    except Exception as e:
        logger.debug(f"PyMuPDF could not extract TOC from PDF: {e}")

    # 2. Fallback: pypdf if available
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.outline:
            def _parse_outlines(outlines):
                entries = []
                for item in outlines:
                    if isinstance(item, list):
                        entries.extend(_parse_outlines(item))
                    elif hasattr(item, "title"):
                        try:
                            p_num = reader.get_destination_page_number(item) + 1
                            title = str(item.title).strip()
                            if title and p_num >= 1:
                                entries.append((title, int(p_num)))
                        except Exception:
                            pass
                return entries
            return _parse_outlines(reader.outline)
    except Exception:
        pass

    return []


class QuadernoDeviceManager:
    """High-level singleton manager for the Quaderno companion bridge."""

    def __init__(self):
        self.router = NetworkRouter()
        self._client: Optional[QuadernoClient] = None
        self._current_route: Optional[DeviceRoute] = None
        self._reading_state = ReadingState()
        self._doc_toc_cache: dict = {}
        self._last_pushed_doc_id: Optional[str] = None
        self._last_pushed_time: float = 0.0
        self._load_persisted_state()
        self._load_persisted_toc_cache()
        # Re-entrant thread lock protects client resolution and route caching across all threads/event loops
        self._sync_lock = threading.RLock()

    def _load_persisted_state(self):
        """Load persistent reading state from disk if in-memory state is empty."""
        try:
            if not self._reading_state.document_id and settings.state_path.exists():
                data = json.loads(settings.state_path.read_text(encoding="utf-8"))
                if data.get("document_id"):
                    self._reading_state = ReadingState(
                        document_id=data.get("document_id"),
                        title=data.get("title"),
                        remote_path=data.get("remote_path"),
                        current_page=int(data.get("current_page", 1)),
                        total_pages=int(data.get("total_pages", 1)),
                        last_updated=datetime.now(),
                    )
        except Exception:
            pass

    def _load_persisted_toc_cache(self):
        """Load persisted TOC cache from disk."""
        try:
            if settings.toc_cache_path.exists():
                data = json.loads(settings.toc_cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, list):
                            self._doc_toc_cache[k] = [(str(item[0]), int(item[1])) for item in v if len(item) >= 2]
        except Exception as e:
            logger.debug(f"Could not load persisted TOC cache: {e}")

    def _save_persisted_toc(self, doc_id: str, toc: List[Tuple[str, int]]):
        """Save a document's TOC to the persisted disk cache."""
        try:
            settings.ensure_directories()
            data = {}
            if settings.toc_cache_path.exists():
                try:
                    data = json.loads(settings.toc_cache_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[doc_id] = [[title, page] for title, page in toc]
            settings.toc_cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save persisted TOC cache: {e}")

    def _save_persisted_state(self):
        """Save current reading state to disk for cross-process sync."""
        try:
            settings.ensure_directories()
            data = {
                "document_id": self._reading_state.document_id,
                "title": self._reading_state.title,
                "remote_path": self._reading_state.remote_path,
                "current_page": self._reading_state.current_page,
                "total_pages": self._reading_state.total_pages,
            }
            settings.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            try:
                os.chmod(settings.state_path, 0o600)
            except Exception:
                pass
        except Exception:
            pass

    @property
    def is_paired(self) -> bool:
        """Check if SSL credentials exist."""
        return settings.device_id_path.exists() and settings.device_key_path.exists()

    @property
    def client(self) -> Optional[QuadernoClient]:
        """Return active QuadernoClient instance (synchronous, thread-safe for WebDAV/WSGI)."""
        if self._client and getattr(self._client, "is_authenticated", False):
            return self._client
        if self.is_paired:
            try:
                return self.get_client_sync()
            except Exception:
                pass
        return self._client

    def get_client_sync(self, force_refresh: bool = False) -> QuadernoClient:
        """Get or initialize active Quaderno client synchronously using the best network route.

        Safe to call from cheroot WSGI threads / WebDAV provider without spawning event loops.
        """
        if not self.is_paired:
            raise DeviceNotPairedError(
                "Device is not paired yet. Please run `quadctl pair --pin <PIN>`."
            )

        with self._sync_lock:
            if not force_refresh and self._client and self._client.is_authenticated and self._current_route:
                if self.router._probe_endpoint_sync(self._current_route.host, self._current_route.port, timeout=1.5):
                    return self._client
                self._client = None
                self._current_route = None
                self.router.invalidate_cache()

            route = self.router.get_active_route_sync(force_refresh=force_refresh)
            if not route:
                raise DeviceNotConnectedError(
                    "Could not find an active Quaderno connection over Wi-Fi, Bluetooth PAN, or USB."
                )

            client = QuadernoClient(host=route.host, port=route.port)
            try:
                client.authenticate_sync()
            except Exception as auth_err:
                self.router.invalidate_cache()
                self._client = None
                self._current_route = None
                raise DeviceNotConnectedError(f"Authentication with Quaderno at {route.host} failed: {auth_err}") from auth_err

            self._current_route = route
            self._client = client
            if route.host and route.host not in ("127.0.0.1", "digitalpaper.local"):
                settings.device_ip = route.host
            return self._client

    async def get_client(self, force_refresh: bool = False) -> QuadernoClient:
        """Get or initialize active Quaderno client using the best network route."""
        return await asyncio.to_thread(self.get_client_sync, force_refresh)

    async def open_document(
        self,
        pdf_bytes: bytes,
        filename: str,
        title: Optional[str] = None,
        page: int = 1,
        remote_folder: Optional[str] = None,
    ) -> OpenDocumentResult:
        """Upload and display a document on the Quaderno screen.

        Args:
            pdf_bytes: Content of the PDF.
            filename: Target filename on Quaderno.
            title: Friendly title for state tracking.
            page: Initial page to display (1-indexed).
            remote_folder: Target directory on device.
        """
        folder = remote_folder or settings.remote_companion_folder
        doc_title = title or filename
        remote_path = f"{folder.strip('/')}/{filename.lstrip('/')}"

        # Inspect local page count and Table of Contents
        total_pages = 1
        raw_toc = []
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = max(1, doc.page_count)
            doc.close()
            raw_toc = extract_pdf_toc(pdf_bytes)
        except Exception as e:
            logger.debug(f"Could not inspect PDF metadata: {e}")

        client = await self.get_client()

        # Step 1: Upload document
        doc_id = await client.upload_document(
            pdf_bytes=pdf_bytes,
            remote_path=remote_path,
        )

        # Step 2: Navigate viewer to target page
        target_page = max(1, min(page, total_pages))
        await client.display_document(document_id=doc_id, page=target_page)

        # Step 3: Update local and persisted reading state
        self._reading_state = ReadingState(
            document_id=doc_id,
            title=doc_title,
            remote_path=remote_path,
            current_page=target_page,
            total_pages=total_pages,
            last_updated=datetime.now(),
        )
        self._last_pushed_doc_id = doc_id
        self._last_pushed_time = time.time()
        self._save_persisted_state()

        if raw_toc:
            self._doc_toc_cache[doc_id] = raw_toc
            self._save_persisted_toc(doc_id, raw_toc)

        logger.info(
            f"Successfully pushed and displayed '{doc_title}' (Page {target_page}/{total_pages}) on Quaderno"
        )
        return {
            "status": "success",
            "document_id": doc_id,
            "title": doc_title,
            "remote_path": remote_path,
            "page": target_page,
            "total_pages": total_pages,
        }

    async def get_toc(self, document_id: Optional[str] = None) -> List[Tuple[str, int]]:
        """Retrieve Table of Contents landmarks for active or specified document."""
        self._load_persisted_state()
        target_id = document_id or self._reading_state.document_id
        if not target_id:
            return []

        if target_id in self._doc_toc_cache:
            return self._doc_toc_cache[target_id]

        self._load_persisted_toc_cache()
        if target_id in self._doc_toc_cache:
            return self._doc_toc_cache[target_id]

        try:
            client = await self.get_client()
            remote_path = self._reading_state.remote_path if target_id == self._reading_state.document_id else None
            lookup_target = target_id or remote_path
            if lookup_target:
                pdf_bytes = await client.download_document_async(lookup_target)
                if pdf_bytes:
                    toc = extract_pdf_toc(pdf_bytes)
                    if toc:
                        self._doc_toc_cache[target_id] = toc
                        self._save_persisted_toc(target_id, toc)
                        return toc
        except Exception as e:
            logger.debug(f"Could not fetch document TOC for {target_id}: {e}")

        return []

    async def navigate(self, action: NavAction, page: Optional[int] = None) -> NavigateResult:
        """Send navigation commands to Quaderno document viewer.

        Args:
            action: 'next', 'prev', 'goto', or 'offset'
            page: Required for 'goto', delta offset for 'offset'.
        """
        client = await self.get_client()

        # Check hardware recent doc state first
        doc_id = self._reading_state.document_id
        curr = self._reading_state.current_page or 1
        total = max(1, self._reading_state.total_pages)
        try:
            recent = await client.get_recent_document()
            if recent and recent.get("entry_id"):
                recent_id = recent["entry_id"]
                if not doc_id or doc_id == recent_id:
                    doc_id = recent_id
                    if recent.get("total_page"):
                        total = int(recent.get("total_page"))
                else:
                    # Switched document on device
                    doc_id = recent_id
                    curr = int(recent.get("current_page", 1))
                    total = int(recent.get("total_page", 1)) if recent.get("total_page") else 1
        except Exception:
            pass

        if not doc_id:
            self._load_persisted_state()
            doc_id = self._reading_state.document_id
            curr = self._reading_state.current_page or 1
            total = max(1, self._reading_state.total_pages)

        if not doc_id:
            raise ValueError("No active document currently open on Quaderno.")

        if action == "next":
            target = min(curr + 1, total)
        elif action == "prev":
            target = max(curr - 1, 1)
        elif action == "goto":
            if page is None:
                raise ValueError("Page number required for 'goto' navigation.")
            target = max(1, min(page, total))
        elif action == "offset":
            delta = page or 0
            target = max(1, min(curr + delta, total))
        else:
            raise ValueError(f"Unknown navigation action: {action}")

        await client.display_document(document_id=doc_id, page=target)

        self._last_nav_time = time.time()
        self._reading_state.document_id = doc_id
        self._reading_state.current_page = target
        self._reading_state.total_pages = total
        self._reading_state.last_updated = datetime.now()
        self._save_persisted_state()

        logger.info(f"Navigated Quaderno viewer to page {target}/{total} ({action})")
        return {
            "status": "success",
            "document_id": doc_id,
            "title": self._reading_state.title or "Document",
            "page": target,
            "total_pages": total,
            "action": action,
        }

    async def get_status(self) -> DeviceStatus:
        """Fetch real-time aggregated status from device and network."""
        self._load_persisted_state()
        status = DeviceStatus(
            is_paired=self.is_paired,
            reading_state=self._reading_state,
        )

        if not self.is_paired:
            return status

        # Fast liveness ping: if currently connected, check if socket is still alive in <=0.6s
        with self._sync_lock:
            current_route = self._current_route
            current_client = self._client

        if current_route and current_client and current_client.is_authenticated:
            is_alive = self.router._probe_endpoint_sync(current_route.host, current_route.port, timeout=0.6)
            if not is_alive:
                logger.debug(f"Fast ping to {current_route.host}:{current_route.port} failed; device powered off/disconnected.")
                with self._sync_lock:
                    self._client = None
                    self._current_route = None
                    self.router.invalidate_cache()
                status.is_connected = False
                return status

        try:
            client = await self.get_client()

            # 1. Fetch battery first (primary liveness verification)
            try:
                bat_res = await client.get_battery_status()
                status.is_connected = True
                if self._current_route:
                    status.connection_type = self._current_route.connection_type
                    status.host = self._current_route.host
                    status.port = self._current_route.port

                if isinstance(bat_res, dict):
                    lvl = bat_res.get("level") or bat_res.get("battery_level")
                    status.battery_level = int(lvl) if lvl is not None else None
                    status.battery_charging = (
                        bat_res.get("status") == "charging" or bat_res.get("charging", False)
                    )
            except Exception as e:
                logger.debug(f"Device unreachable during status check: {e}")
                with self._sync_lock:
                    self._client = None
                    self._current_route = None
                    self.router.invalidate_cache()
                status.is_connected = False
                return status

            # 2. Storage status (best effort)
            try:
                storage_res = await client.get_storage_status()
                if isinstance(storage_res, dict):
                    total_b = storage_res.get("total_space") or storage_res.get("capacity")
                    free_b = storage_res.get("free_space") or storage_res.get("available")
                    if total_b is not None:
                        status.storage_total_mb = round(float(total_b) / (1024 * 1024), 1)
                    if free_b is not None:
                        status.storage_free_mb = round(float(free_b) / (1024 * 1024), 1)
            except Exception as storage_err:
                logger.debug(f"Storage status query failed (non-critical): {storage_err}")

            # 3. Synchronize reading state live from Quaderno hardware (best effort)
            try:
                recent_res = await client.get_recent_document()
                if isinstance(recent_res, dict) and recent_res.get("entry_id"):
                    recent_id = recent_res["entry_id"]
                    is_recent_nav = (time.time() - getattr(self, "_last_nav_time", 0.0)) < 5.0
                    is_recent_push = (time.time() - getattr(self, "_last_pushed_time", 0.0)) < 300.0
                    if not is_recent_push or recent_id == getattr(self, "_last_pushed_doc_id", None):
                        tot_p = int(recent_res.get("total_page", 1)) if recent_res.get("total_page") else max(1, self._reading_state.total_pages)
                        doc_name = recent_res.get("entry_name") or recent_res.get("title") or self._reading_state.title or "Document"
                        if is_recent_nav and self._reading_state.document_id == recent_id:
                            cur_p = self._reading_state.current_page
                        else:
                            cur_p = int(recent_res.get("current_page", 1))

                        self._reading_state = ReadingState(
                            document_id=recent_id,
                            title=doc_name,
                            remote_path=recent_res.get("entry_path", ""),
                            current_page=cur_p,
                            total_pages=tot_p,
                            last_updated=datetime.now(),
                        )
                        self._save_persisted_state()
            except Exception as recent_err:
                logger.debug(f"Recent document sync failed (non-critical): {recent_err}")

            status.reading_state = self._reading_state

        except Exception as e:
            logger.debug(f"Device not currently connected during status query: {e}")
            with self._sync_lock:
                self._client = None
                self._current_route = None
                self.router.invalidate_cache()
            status.is_connected = False

        return status

    async def pair_device(self, pin: Optional[str] = None, host: Optional[str] = None) -> Tuple[str, str]:
        """Pair with Quaderno using PIN."""
        target_host = host or settings.device_ip
        if not target_host:
            route = await self.router.get_active_route()
            if route:
                target_host = route.host

        client = QuadernoClient(host=target_host or "127.0.0.1", port=settings.device_port)
        return await client.register(pin=pin)


# Global singleton instance
device_manager = QuadernoDeviceManager()
