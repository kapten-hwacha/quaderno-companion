"""Unit tests for Last Pushed Document Tracker & State persistence."""

from unittest.mock import patch
import pytest
from quaderno_companion.state import (
    clear_last_pushed_document,
    get_last_pushed_document,
    record_pushed_document,
)


def test_record_and_get_last_pushed_document(tmp_path):
    """Verify recording and reading metadata for last pushed document."""
    with patch("quaderno_companion.state._get_state_file", return_value=tmp_path / "last_pushed.json"):
        # Initial empty state
        assert get_last_pushed_document() is None

        # Record a document
        record_pushed_document(
            doc_id="doc-abc-123",
            title="E-ink Paper",
            path="https://en.wikipedia.org/wiki/E-ink",
        )

        doc = get_last_pushed_document()
        assert doc is not None
        assert doc["doc_id"] == "doc-abc-123"
        assert doc["title"] == "E-ink Paper"
        assert doc["path"] == "https://en.wikipedia.org/wiki/E-ink"
        assert "timestamp" in doc

        # Overwrite with another document
        record_pushed_document(
            doc_id="doc-xyz-789",
            title="Quantum Computing",
            path="/tmp/qc.pdf",
        )

        doc2 = get_last_pushed_document()
        assert doc2 is not None
        assert doc2["doc_id"] == "doc-xyz-789"
        assert doc2["title"] == "Quantum Computing"

        # Clear state
        clear_last_pushed_document()
        assert get_last_pushed_document() is None
