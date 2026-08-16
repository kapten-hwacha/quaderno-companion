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
                return self._client

            route = self.router.get_active_route_sync(force_refresh=force_refresh)
            if not route:
                raise DeviceNotConnectedError(
                    "Could not find an active Quaderno connection over Wi-Fi, Bluetooth PAN, or USB."
                )

            client = QuadernoClient(host=route.host, port=route.port)
            client.authenticate_sync()
            self._current_route = route
            self._client = client
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
            temp_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(temp_doc)
            raw_toc = temp_doc.get_toc() or []
            temp_doc.close()
        except Exception:
            pass

        client = await self.get_client()

        # Upload document
        upload_result = await client.upload_document(
            pdf_data=pdf_bytes,
            remote_filename=filename,
            remote_folder=folder,
        )

        # Retrieve document ID
        doc_id = None
        if isinstance(upload_result, dict):
            doc_id = upload_result.get("entry_id") or upload_result.get("document_id")
        if not doc_id:
            doc_id = await client.resolve_document_id(remote_path)

        if not doc_id:
            raise RuntimeError(f"Could not resolve document_id for uploaded file {remote_path}")

        if raw_toc:
            self._doc_toc_cache[doc_id] = [
                (item[1].strip(), int(item[2]))
                for item in raw_toc
                if len(item) >= 3 and item[1].strip()
            ]

        # Command viewer to open document at specified page
        target_page = max(1, min(page, total_pages))
        await client.display_document(document_id=doc_id, page=target_page)

        # Update tracked state
        self._last_pushed_doc_id = doc_id
        self._last_pushed_time = time.time()
        self._reading_state = ReadingState(
            document_id=doc_id,
            title=doc_title,
            remote_path=remote_path,
            current_page=target_page,
            total_pages=total_pages,
            last_updated=datetime.now(),
        )
        self._save_persisted_state()

        logger.info(f"Opened '{doc_title}' on Quaderno (Page {target_page}/{total_pages})")
        return {
            "status": "success",
            "document_id": doc_id,
            "title": doc_title,
            "remote_path": remote_path,
            "page": target_page,
            "total_pages": total_pages,
        }

    async def get_document_toc(self, doc_id: Optional[str] = None) -> List[Tuple[str, int]]:
        """Retrieve Table of Contents (chapters and page numbers) for the specified or active document."""
        if not doc_id and self._reading_state:
            doc_id = self._reading_state.document_id

        if not doc_id:
            return []

        return self._doc_toc_cache.get(doc_id, [])

    async def navigate(self, action: NavAction, page: Optional[int] = None) -> NavigateResult:
        """Navigate pages in the currently active document without re-uploading."""
        client = await self.get_client()
        
        doc_id = self._reading_state.document_id
        curr = self._reading_state.current_page
        total = max(1, self._reading_state.total_pages)

        # If no in-memory state, query hardware
        if not doc_id:
            recent = None
            try:
                recent = await client.get_recent_document()
            except Exception:
                pass

            if recent and recent.get("entry_id"):
                doc_id = recent["entry_id"]
                curr = int(recent.get("current_page", 1))
                total = int(recent.get("total_page", 1)) if recent.get("total_page") else 1
                doc_name = recent.get("entry_name") or recent.get("title") or "Document"
                self._reading_state = ReadingState(
                    document_id=doc_id,
                    title=doc_name,
                    remote_path=recent.get("entry_path", ""),
                    current_page=curr,
                    total_pages=total,
                    last_updated=datetime.now(),
                )
            else:
                self._load_persisted_state()
                doc_id = self._reading_state.document_id
                curr = self._reading_state.current_page
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

        self._reading_state.current_page = target
        self._reading_state.last_updated = datetime.now()
        self._save_persisted_state()

        logger.info(f"Navigated Quaderno viewer to page {target}/{total} ({action})")
        return {
            "status": "success",
            "document_id": doc_id,
            "title": self._reading_state.title,
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

        try:
            client = await self.get_client()
            status.is_connected = True
            if self._current_route:
                status.connection_type = self._current_route.connection_type
                status.host = self._current_route.host
                status.port = self._current_route.port

            # Battery
            try:
                bat = await client.get_battery_status()
                lvl = bat.get("level") or bat.get("battery_level")
                status.battery_level = int(lvl) if lvl is not None else None
                status.battery_charging = (
                    bat.get("status") == "charging" or bat.get("charging", False)
                )
            except Exception:
                pass

            # Storage
            try:
                storage = await client.get_storage_status()
                total_b = storage.get("total_space") or storage.get("capacity")
                free_b = storage.get("free_space") or storage.get("available")
                if total_b is not None:
                    status.storage_total_mb = round(float(total_b) / (1024 * 1024), 1)
                if free_b is not None:
                    status.storage_free_mb = round(float(free_b) / (1024 * 1024), 1)
            except Exception:
                pass

            # Synchronize reading state live from Quaderno hardware
            try:
                is_recent_push = (time.time() - getattr(self, "_last_pushed_time", 0.0)) < 300.0
                recent = await client.get_recent_document()
                if recent and recent.get("entry_id"):
                    recent_id = recent["entry_id"]
                    if not is_recent_push or recent_id == getattr(self, "_last_pushed_doc_id", None):
                        cur_p = int(recent.get("current_page", 1))
                        tot_p = int(recent.get("total_page", 1)) if recent.get("total_page") else 1
                        doc_name = recent.get("entry_name") or recent.get("title") or "Document"
                        self._reading_state = ReadingState(
                            document_id=recent_id,
                            title=doc_name,
                            remote_path=recent.get("entry_path", ""),
                            current_page=cur_p,
                            total_pages=tot_p,
                            last_updated=datetime.now(),
                        )
                        self._save_persisted_state()
                status.reading_state = self._reading_state
            except Exception as e:
                logger.debug(f"Failed to fetch recent doc from device: {e}")

        except Exception as e:
            logger.debug(f"Device not currently connected during status query: {e}")
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
