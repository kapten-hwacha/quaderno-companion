"""Offline Push Queue Manager for Quaderno Companion.

Manages persistent queue of documents waiting to be pushed to the Quaderno device
when offline, and handles automatic/manual queue flushing when the device connects.
"""

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QueuedItem:
    """A document queued for push to Quaderno."""
    id: str
    title: str
    filename: str
    remote_folder: str
    page: int
    source: str
    staged_path: str
    created_at: float = field(default_factory=time.time)
    file_size: int = 0
    clean_prev: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueuedItem":
        return cls(
            id=str(data["id"]),
            title=str(data.get("title", "Document")),
            filename=str(data.get("filename", "document.pdf")),
            remote_folder=str(data.get("remote_folder", "Document/Companion")),
            page=int(data.get("page", 1)),
            source=str(data.get("source", "")),
            staged_path=str(data.get("staged_path", "")),
            created_at=float(data.get("created_at", time.time())),
            file_size=int(data.get("file_size", 0)),
            clean_prev=bool(data.get("clean_prev", False)),
        )


@dataclass
class FlushResult:
    """Summary of a push queue flush operation."""
    flushed: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)
    remaining: int = 0
    device_connected: bool = True


class PushQueueManager:
    """Thread-safe persistent push queue manager."""

    def __init__(self):
        self._lock = threading.RLock()

    def _ensure_dirs(self) -> None:
        settings.ensure_directories()

    def _load_queue(self) -> List[QueuedItem]:
        """Load queued items from disk."""
        self._ensure_dirs()
        path = settings.queue_path
        if not path.exists():
            return []

        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, list):
                return []

            items = []
            for entry in data:
                try:
                    item = QueuedItem.from_dict(entry)
                    if Path(item.staged_path).exists():
                        items.append(item)
                    else:
                        logger.warning(f"Pruning queued item '{item.title}' (staged file missing: {item.staged_path})")
                except Exception as e:
                    logger.debug(f"Invalid queue item entry: {e}")
            return items
        except Exception as e:
            logger.error(f"Failed to read push queue file {path}: {e}")
            return []

    def _save_queue(self, items: List[QueuedItem]) -> None:
        """Save queued items atomically to disk."""
        self._ensure_dirs()
        path = settings.queue_path
        tmp_path = path.with_suffix(".tmp")
        try:
            raw = [item.to_dict() for item in items]
            tmp_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except Exception:
                pass
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error(f"Failed to write push queue file {path}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def list_items(self) -> List[QueuedItem]:
        """Return all pending queued items in FIFO order."""
        with self._lock:
            return self._load_queue()

    def count(self) -> int:
        """Return the number of queued items."""
        with self._lock:
            return len(self._load_queue())

    def has_items(self) -> bool:
        """Return True if there are queued items waiting."""
        return self.count() > 0

    def remove(self, item_id: str) -> bool:
        """Remove a queued item by ID and clean up its staged file."""
        with self._lock:
            items = self._load_queue()
            target = None
            remaining = []
            for item in items:
                if item.id == item_id:
                    target = item
                else:
                    remaining.append(item)

            if target:
                staged = Path(target.staged_path)
                if staged.exists():
                    try:
                        staged.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Could not delete staged file {staged}: {e}")
                self._save_queue(remaining)
                logger.info(f"Removed queued item '{target.title}' (ID: {item_id})")
                return True
            return False

    def clear(self) -> int:
        """Remove all queued items and delete staged files."""
        with self._lock:
            items = self._load_queue()
            count = len(items)
            for item in items:
                try:
                    Path(item.staged_path).unlink(missing_ok=True)
                except Exception:
                    pass

            self._save_queue([])

            # Also sweep any orphaned files in queue cache directory
            if settings.queue_cache_dir.exists():
                for p in settings.queue_cache_dir.glob("*.pdf"):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

            logger.info(f"Cleared {count} item(s) from push queue")
            return count

    def enqueue_bytes(
        self,
        pdf_bytes: bytes,
        filename: str,
        title: Optional[str] = None,
        page: int = 1,
        destination_folder: Optional[str] = None,
        source: str = "",
        clean_prev: bool = False,
    ) -> QueuedItem:
        """Enqueue pre-rendered PDF bytes for later push."""
        self._ensure_dirs()
        item_id = f"q_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        staged_path = settings.queue_cache_dir / f"{item_id}.pdf"

        # Write staged PDF
        staged_path.write_bytes(pdf_bytes)
        try:
            os.chmod(staged_path, 0o600)
        except Exception:
            pass

        raw_folder = destination_folder or settings.remote_companion_folder
        fldr = raw_folder.strip("/")
        if not fldr or fldr.lower() == "document":
            folder = "Document"
        elif not fldr.lower().startswith("document/"):
            folder = f"Document/{fldr}"
        else:
            folder = fldr

        doc_title = title or Path(filename).stem or "Document"
        clean_filename = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"

        item = QueuedItem(
            id=item_id,
            title=doc_title,
            filename=clean_filename,
            remote_folder=folder,
            page=max(1, page),
            source=source or clean_filename,
            staged_path=str(staged_path),
            created_at=time.time(),
            file_size=len(pdf_bytes),
            clean_prev=clean_prev,
        )

        with self._lock:
            items = self._load_queue()
            items.append(item)
            self._save_queue(items)

        logger.info(f"Queued document '{doc_title}' ({item.file_size} bytes) in {folder} (ID: {item_id})")
        return item

    async def enqueue(
        self,
        source_url_or_path: str,
        title: Optional[str] = None,
        page: int = 1,
        profile: Optional[str] = None,
        destination_folder: Optional[str] = None,
        clean_prev: bool = False,
    ) -> QueuedItem:
        """Fetch, optimize, and enqueue a document or URL for later push."""
        from quaderno_companion.pipeline.fetcher import ContentFetcher

        target_profile = profile or settings.default_profile
        fetcher = ContentFetcher(profile_name=target_profile)

        doc = await fetcher.fetch(
            source_url_or_path=source_url_or_path,
            custom_title=title,
            optimize_for_eink=True,
        )

        return self.enqueue_bytes(
            pdf_bytes=doc.pdf_bytes,
            filename=doc.filename,
            title=doc.title,
            page=page,
            destination_folder=destination_folder,
            source=source_url_or_path,
            clean_prev=clean_prev,
        )

    def enqueue_sync(
        self,
        source_url_or_path: str,
        title: Optional[str] = None,
        page: int = 1,
        profile: Optional[str] = None,
        destination_folder: Optional[str] = None,
        clean_prev: bool = False,
    ) -> QueuedItem:
        """Synchronous helper for enqueuing files from non-async contexts."""
        from quaderno_companion.pipeline.fetcher import ContentFetcher

        target_profile = profile or settings.default_profile
        fetcher = ContentFetcher(profile_name=target_profile)

        doc = asyncio.run(fetcher.fetch(
            source_url_or_path=source_url_or_path,
            custom_title=title,
            optimize_for_eink=True,
        ))

        return self.enqueue_bytes(
            pdf_bytes=doc.pdf_bytes,
            filename=doc.filename,
            title=doc.title,
            page=page,
            destination_folder=destination_folder,
            source=source_url_or_path,
            clean_prev=clean_prev,
        )

    def flush_sync(
        self,
        client: Optional[Any] = None,
        device_mgr: Optional[Any] = None,
    ) -> FlushResult:
        """Process and push all queued documents synchronously to Quaderno."""
        with self._lock:
            items = self._load_queue()
            if not items:
                return FlushResult(flushed=[], failed=[], remaining=0, device_connected=True)

            mgr = device_mgr
            if mgr is None:
                from quaderno_companion.device.manager import device_manager
                mgr = device_manager

            active_client = client
            if active_client is None:
                try:
                    active_client = mgr.get_client_sync()
                except Exception as e:
                    logger.debug(f"Queue flush skipped (device not connected): {e}")
                    return FlushResult(flushed=[], failed=[], remaining=len(items), device_connected=False)

            result = FlushResult()
            remaining_items = list(items)

            # Separate tracking for the last successfully uploaded item so we display it
            last_pushed_info: Optional[Dict[str, Any]] = None

            for item in items:
                staged = Path(item.staged_path)
                if not staged.exists():
                    logger.warning(f"Staged file {staged} missing for queued item {item.id}, skipping.")
                    remaining_items.remove(item)
                    self._save_queue(remaining_items)
                    continue

                remote_path = f"{item.remote_folder}/{item.filename.lstrip('/')}"
                try:
                    pdf_bytes = staged.read_bytes()
                    # Support both client API variations (upload_document_sync vs upload_document)
                    if hasattr(active_client, "upload_document_sync"):
                        doc_id = active_client.upload_document_sync(
                            pdf_data=pdf_bytes,
                            filename=item.filename,
                            folder=item.remote_folder,
                        )
                    else:
                        doc_id = asyncio.run(active_client.upload_document(
                            pdf_bytes=pdf_bytes,
                            remote_path=remote_path,
                        ))

                    last_pushed_info = {
                        "doc_id": doc_id,
                        "title": item.title,
                        "remote_path": remote_path,
                        "page": item.page,
                        "source": item.source,
                        "clean_prev": item.clean_prev,
                    }

                    # Remove staged file & update queue immediately on success
                    staged.unlink(missing_ok=True)
                    remaining_items.remove(item)
                    self._save_queue(remaining_items)

                    result.flushed.append({
                        "id": item.id,
                        "doc_id": doc_id,
                        "title": item.title,
                        "remote_path": remote_path,
                    })
                    logger.info(f"Successfully pushed queued document '{item.title}' to {remote_path} (ID: {doc_id})")

                except Exception as err:
                    logger.error(f"Failed to push queued document '{item.title}': {err}")
                    result.failed.append({
                        "id": item.id,
                        "title": item.title,
                        "error": str(err),
                    })
                    # Stop processing further items on connection or device error
                    break

            # If at least one item was pushed, display the final document on Quaderno screen
            if last_pushed_info and active_client:
                try:
                    doc_id = last_pushed_info["doc_id"]
                    target_page = last_pushed_info["page"]
                    if hasattr(active_client, "display_document_sync"):
                        active_client.display_document_sync(document_id=doc_id, page=target_page)
                    else:
                        asyncio.run(active_client.display_document(document_id=doc_id, page=target_page))

                    from quaderno_companion.state import record_pushed_document
                    record_pushed_document(
                        doc_id=doc_id,
                        title=last_pushed_info["title"],
                        path=last_pushed_info["source"],
                    )

                    if hasattr(mgr, "_reading_state"):
                        from quaderno_companion.device.manager import ReadingState
                        mgr._reading_state = ReadingState(
                            document_id=doc_id,
                            title=last_pushed_info["title"],
                            remote_path=last_pushed_info["remote_path"],
                            current_page=target_page,
                            total_pages=1,
                            last_updated=datetime.now(),
                        )
                        mgr._last_pushed_doc_id = doc_id
                        mgr._last_pushed_time = time.time()
                        mgr._save_persisted_state()

                except Exception as disp_err:
                    logger.warning(f"Could not switch display to queued document: {disp_err}")

            result.remaining = len(remaining_items)
            return result

    async def flush(
        self,
        client: Optional[Any] = None,
        device_mgr: Optional[Any] = None,
    ) -> FlushResult:
        """Process and push all queued documents asynchronously to Quaderno."""
        return await asyncio.to_thread(self.flush_sync, client, device_mgr)


# Global singleton instance
push_queue = PushQueueManager()
