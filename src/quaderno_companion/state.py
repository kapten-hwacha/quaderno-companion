"""Persistent Companion State & Last Pushed Document Tracker."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from quaderno_companion.config import settings

logger = logging.getLogger(__name__)


def _get_state_file():
    state_dir = settings.config_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "last_pushed.json"


def get_last_pushed_document() -> Optional[Dict[str, Any]]:
    """Retrieve metadata about the previously pushed document."""
    state_file = _get_state_file()
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if data and data.get("doc_id"):
            return data
    except Exception as e:
        logger.debug(f"Failed to read last_pushed.json: {e}")
    return None


def record_pushed_document(doc_id: str, title: str, path: str = ""):
    """Record metadata of newly pushed document."""
    state_file = _get_state_file()
    data = {
        "doc_id": str(doc_id),
        "title": str(title or Path(path).stem or "Document"),
        "path": str(path),
        "timestamp": time.time(),
    }
    try:
        state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info(f"Recorded last pushed document: '{data['title']}' (ID: {doc_id})")
    except Exception as e:
        logger.warning(f"Failed to write last_pushed.json: {e}")


def clear_last_pushed_document():
    """Clear last pushed document record."""
    state_file = _get_state_file()
    try:
        if state_file.exists():
            state_file.unlink()
    except Exception as e:
        logger.warning(f"Failed to clear last_pushed.json: {e}")
