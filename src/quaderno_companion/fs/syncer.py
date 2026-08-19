"""Bidirectional Local Folder Mirror Sync Engine for Quaderno Companion."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from quaderno_companion.config import settings
from quaderno_companion.device.manager import device_manager
from quaderno_companion.pipeline.optimizer import EinkOptimizer

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Summary of a single bidirectional sync pass."""
    pulled: List[str] = field(default_factory=list)
    pushed: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pulled": self.pulled,
            "pushed": self.pushed,
            "deleted": self.deleted,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "timestamp": self.timestamp,
            "iso_time": datetime.fromtimestamp(self.timestamp).isoformat(),
        }


def _norm_remote_path(p: Optional[str]) -> str:
    """Normalize Quaderno remote path (removes leading Document/ prefix)."""
    p = (p or "").strip("/")
    if p.lower() == "document":
        return ""
    if p.lower().startswith("document/"):
        return p[9:]
    return p


def _to_remote_folder(rel_folder: str) -> str:
    """Convert relative folder path to Quaderno remote folder path (prepends Document/)."""
    rel_folder = rel_folder.strip("/")
    if not rel_folder:
        return "Document"
    if rel_folder.lower().startswith("document/"):
        return rel_folder
    return f"Document/{rel_folder}"


def _compute_file_sha256(path: Path) -> str:
    """Compute SHA256 checksum of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _download_remote_to_file(client: Any, doc_id: str, dest_path: Path) -> None:
    """Download remote document to disk using streaming if available, with in-memory fallback."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(client, "download_document_to_file"):
        try:
            client.download_document_to_file(doc_id, dest_path)
            if dest_path.exists() and dest_path.stat().st_size > 0:
                return
        except Exception:
            pass
    data = client.download_document(doc_id)
    if isinstance(data, (bytes, bytearray)):
        with open(dest_path, "wb") as f:
            f.write(data)


class QuadernoSyncer:
    """Bidirectional folder sync engine matching local directory to Quaderno storage."""

    def __init__(
        self,
        sync_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self.sync_dir = sync_dir or settings.sync_dir
        self.state_path = state_path or settings.sync_state_path
        self.optimizer = EinkOptimizer()

    def _load_state(self) -> Dict[str, Any]:
        """Load sync state database from JSON file."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load sync state ({e}), starting fresh.")
        return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        """Save sync state database to JSON file atomically with secure permissions."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            try:
                os.chmod(tmp_path, 0o600)
            except Exception:
                pass
            tmp_path.replace(self.state_path)
            try:
                os.chmod(self.state_path, 0o600)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to save sync state: {e}")

    def sync_pass(self, client: Optional[Any] = None) -> SyncResult:
        """Execute one bidirectional synchronization pass."""
        result = SyncResult()
        self.sync_dir.mkdir(parents=True, exist_ok=True)

        if client is None:
            try:
                client = device_manager.client
            except Exception as e:
                err = f"Device client unavailable: {e}"
                logger.warning(err)
                result.errors.append(err)
                return result

        if client is None:
            err = "Quaderno device client is not connected."
            logger.debug(err)
            result.errors.append(err)
            return result

        state = self._load_state()

        # 1. Fetch Remote File & Folder Map
        try:
            raw_docs = client.list_all_documents()
        except Exception as e:
            err = f"Failed to list Quaderno documents: {e}"
            logger.error(err)
            result.errors.append(err)
            return result

        remote_folders: Dict[str, Dict[str, Any]] = {}
        remote_files: Dict[str, Dict[str, Any]] = {}

        for item in raw_docs:
            entry_path = item.get("entry_path", "")
            rel_path = _norm_remote_path(entry_path)
            if not rel_path:
                continue
            entry_type = item.get("entry_type", "document")
            if entry_type == "folder":
                remote_folders[rel_path] = item
            else:
                remote_files[rel_path] = item

        # 2. Scan Local Directory Structure
        local_files: Dict[str, Path] = {}
        local_folders: Set[str] = set()

        for root, dirs, files in os.walk(self.sync_dir):
            rel_root = os.path.relpath(root, self.sync_dir)
            if rel_root == ".":
                rel_root = ""

            # Filter out hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if rel_root:
                local_folders.add(rel_root.replace("\\", "/"))

            for f in files:
                if f.startswith(".") or f.endswith(".tmp") or f.endswith(".icloud"):
                    continue
                rel_file = os.path.join(rel_root, f).replace("\\", "/") if rel_root else f
                local_files[rel_file] = Path(root) / f

        # 3. Synchronize Folders First
        # 3a. Create missing local folders matching remote structure
        for r_folder in remote_folders:
            local_target = self.sync_dir / r_folder
            if not local_target.exists():
                try:
                    local_target.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    err = f"Failed to create local directory {r_folder}: {e}"
                    logger.error(err)
                    result.errors.append(err)

        # 3b. Create missing remote folders matching local subdirectories
        for l_folder in sorted(local_folders):
            if l_folder not in remote_folders:
                try:
                    r_folder = _to_remote_folder(l_folder)
                    client.create_folder_sync(r_folder)
                    remote_folders[l_folder] = {"entry_path": r_folder, "entry_type": "folder"}
                    logger.info(f"Created remote Quaderno folder: {r_folder}")
                except Exception as e:
                    err = f"Failed to create remote folder '{l_folder}': {e}"
                    logger.error(err)
                    result.errors.append(err)

        # 4. Process Remote Files (Pull & Deletion Propagation)
        for rel_path, r_info in remote_files.items():
            doc_id_raw = r_info.get("entry_id") or r_info.get("document_id")
            if not doc_id_raw:
                continue
            doc_id = str(doc_id_raw)
            r_size = r_info.get("file_size", 0)
            r_mtime = str(r_info.get("modified_date", ""))
            local_path = self.sync_dir / rel_path

            prev_state = state.get(rel_path, {})

            if not local_path.exists():
                # Check if deleted locally after previously being synced
                if prev_state and prev_state.get("doc_id") == doc_id:
                    # File was deleted locally -> propagate deletion to remote
                    try:
                        client.delete_document(doc_id)
                        result.deleted.append(rel_path)
                        state.pop(rel_path, None)
                        logger.info(f"Propagated local deletion to Quaderno: {rel_path}")
                        continue
                    except Exception as e:
                        err = f"Failed to delete remote document '{rel_path}': {e}"
                        logger.error(err)
                        result.errors.append(err)

                # Download new remote document
                try:
                    _download_remote_to_file(client, doc_id, local_path)
                    result.pulled.append(rel_path)
                    logger.info(f"Pulled document from Quaderno: {rel_path}")
                    
                    loc_mtime = local_path.stat().st_mtime
                    loc_sha = _compute_file_sha256(local_path)
                    state[rel_path] = {
                        "doc_id": doc_id,
                        "remote_mtime": r_mtime,
                        "file_size": r_size,
                        "local_mtime": loc_mtime,
                        "local_sha256": loc_sha,
                    }
                except Exception as e:
                    err = f"Failed to download '{rel_path}': {e}"
                    logger.error(err)
                    result.errors.append(err)
            else:
                # File exists both locally and remotely
                loc_mtime = local_path.stat().st_mtime
                loc_sha = _compute_file_sha256(local_path)

                remote_changed = (r_mtime != prev_state.get("remote_mtime")) or (r_size != prev_state.get("file_size"))
                local_changed = (loc_sha != prev_state.get("local_sha256"))

                if remote_changed and not local_changed:
                    # Download remote update
                    try:
                        _download_remote_to_file(client, doc_id, local_path)
                        result.pulled.append(rel_path)
                        logger.info(f"Pulled updated document from Quaderno: {rel_path}")
                        state[rel_path] = {
                            "doc_id": doc_id,
                            "remote_mtime": r_mtime,
                            "file_size": r_size,
                            "local_mtime": local_path.stat().st_mtime,
                            "local_sha256": _compute_file_sha256(local_path),
                        }
                    except Exception as e:
                        err = f"Failed to pull update for '{rel_path}': {e}"
                        logger.error(err)
                        result.errors.append(err)

                elif local_changed and not remote_changed:
                    # Upload local update
                    try:
                        r_folder = _to_remote_folder(os.path.dirname(rel_path))
                        filename = local_path.name
                        new_id = client.upload_document_sync(local_path, filename=filename, folder=r_folder)
                        result.pushed.append(rel_path)
                        logger.info(f"Pushed updated document to Quaderno: {rel_path}")
                        state[rel_path] = {
                            "doc_id": new_id or doc_id,
                            "remote_mtime": r_mtime,
                            "file_size": local_path.stat().st_size,
                            "local_mtime": loc_mtime,
                            "local_sha256": loc_sha,
                        }
                    except Exception as e:
                        err = f"Failed to push update for '{rel_path}': {e}"
                        logger.error(err)
                        result.errors.append(err)

                elif remote_changed and local_changed:
                    # Conflict: download remote copy with timestamp suffix, push local file
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        stem, ext = os.path.splitext(rel_path)
                        conflict_rel = f"{stem} (Quaderno Conflict {ts}){ext}"
                        conflict_local = self.sync_dir / conflict_rel
                        
                        _download_remote_to_file(client, doc_id, conflict_local)
                        result.pulled.append(conflict_rel)

                        r_folder = _to_remote_folder(os.path.dirname(rel_path))
                        filename = local_path.name
                        new_id = client.upload_document_sync(local_path, filename=filename, folder=r_folder)
                        result.pushed.append(rel_path)
                        result.conflicts.append(rel_path)
                        logger.warning(f"Resolved sync conflict for '{rel_path}' by creating conflict copy")

                        state[rel_path] = {
                            "doc_id": new_id or doc_id,
                            "remote_mtime": r_mtime,
                            "file_size": local_path.stat().st_size,
                            "local_mtime": loc_mtime,
                            "local_sha256": loc_sha,
                        }
                    except Exception as e:
                        err = f"Failed to resolve conflict for '{rel_path}': {e}"
                        logger.error(err)
                        result.errors.append(err)

        # 5. Process New Local Files (Not on Remote)
        for rel_path, local_path in local_files.items():
            if rel_path in remote_files:
                continue

            prev_state = state.get(rel_path, {})
            if prev_state and prev_state.get("doc_id"):
                # Was on remote, deleted on remote -> propagate local deletion
                try:
                    local_path.unlink()
                    result.deleted.append(rel_path)
                    state.pop(rel_path, None)
                    logger.info(f"Propagated remote deletion to local file: {rel_path}")
                    continue
                except Exception as e:
                    err = f"Failed to delete local file '{rel_path}': {e}"
                    logger.error(err)
                    result.errors.append(err)

            # New local file -> push to Quaderno
            try:
                parent_folder = os.path.dirname(rel_path)
                r_folder = _to_remote_folder(parent_folder)
                filename = local_path.name

                # Ensure remote folder exists
                if parent_folder and parent_folder not in remote_folders:
                    try:
                        client.create_folder_sync(r_folder)
                        remote_folders[parent_folder] = {"entry_path": r_folder, "entry_type": "folder"}
                    except Exception:
                        pass

                # Non-PDF optimization/conversion
                if local_path.suffix.lower() != ".pdf":
                    pdf_bytes, target_filename = self.optimizer.optimize_file(local_path)
                    new_id = client.upload_document_sync(pdf_bytes, filename=target_filename, folder=r_folder)
                else:
                    new_id = client.upload_document_sync(local_path, filename=filename, folder=r_folder)

                result.pushed.append(rel_path)
                logger.info(f"Pushed new local file to Quaderno: {rel_path} (ID: {new_id})")

                state[rel_path] = {
                    "doc_id": new_id,
                    "remote_mtime": datetime.now().isoformat(),
                    "file_size": local_path.stat().st_size,
                    "local_mtime": local_path.stat().st_mtime,
                    "local_sha256": _compute_file_sha256(local_path),
                }
            except Exception as e:
                err = f"Failed to push new file '{rel_path}': {e}"
                logger.error(err)
                result.errors.append(err)

        self._save_state(state)
        return result


class QuadernoSyncRunner:
    """Background runner performing periodic sync passes."""

    def __init__(self, syncer: Optional[QuadernoSyncer] = None, interval: int = 30):
        self.syncer = syncer or QuadernoSyncer()
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_result: Optional[SyncResult] = None
        self.is_running = False

    def start(self) -> None:
        """Start periodic background sync thread."""
        if self.is_running:
            return

        self._stop_event.clear()
        self.is_running = True

        def _worker():
            logger.info(f"Started Quaderno background folder sync (interval: {self.interval}s)")
            while not self._stop_event.is_set():
                try:
                    res = self.syncer.sync_pass()
                    self.last_result = res
                    if res.pulled or res.pushed or res.deleted:
                        logger.info(
                            f"Sync complete: pulled={len(res.pulled)}, pushed={len(res.pushed)}, deleted={len(res.deleted)}"
                        )
                except Exception as e:
                    logger.debug(f"Background sync pass skipped: {e}")

                self._stop_event.wait(self.interval)

            self.is_running = False
            logger.info("Stopped Quaderno background folder sync")

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background sync thread."""
        if not self.is_running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.is_running = False


# Global syncer instances
syncer = QuadernoSyncer()
sync_runner = QuadernoSyncRunner(syncer=syncer)
