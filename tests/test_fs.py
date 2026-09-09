"""Unit tests for Quaderno Bidirectional Local Folder Sync Engine."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from quaderno_companion.device.manager import device_manager
from quaderno_companion.fs.syncer import QuadernoSyncRunner, QuadernoSyncer, SyncResult


@pytest.fixture
def mock_quaderno_client():
    mock = MagicMock()
    mock.list_all_documents.return_value = [
        {"entry_id": "root", "entry_name": "Document", "entry_path": "Document", "entry_type": "folder"},
        {"entry_id": "f-companion", "entry_name": "Companion", "entry_path": "Document/Companion", "entry_type": "folder", "parent_folder_id": "root"},
        {"entry_id": "doc-1", "entry_name": "control_systems.pdf", "entry_path": "Document/control_systems.pdf", "entry_type": "document", "file_size": 2048, "modified_date": "2026-08-16T10:00:00Z"},
        {"entry_id": "doc-2", "entry_name": "quick_note.pdf", "entry_path": "Document/Companion/quick_note.pdf", "entry_type": "document", "file_size": 4096, "modified_date": "2026-08-16T10:00:00Z"},
    ]
    mock.download_document.return_value = b"%PDF-1.4 test document content"
    mock.upload_document_sync.return_value = "doc-new-123"
    mock.delete_document.return_value = True
    mock.create_folder_sync.return_value = None

    with patch.object(device_manager, "_client", mock):
        yield mock


def test_syncer_pull_remote_documents(tmp_path, mock_quaderno_client):
    """Verify downloading remote files and creating local folders."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    res = syncer.sync_pass(client=mock_quaderno_client)

    assert "control_systems.pdf" in res.pulled
    assert "Companion/quick_note.pdf" in res.pulled
    assert (sync_dir / "control_systems.pdf").exists()
    assert (sync_dir / "Companion" / "quick_note.pdf").exists()
    assert (sync_dir / "control_systems.pdf").read_bytes().startswith(b"%PDF-1.4")


def test_syncer_push_local_document(tmp_path, mock_quaderno_client):
    """Verify uploading a new local PDF file to Quaderno."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"
    sync_dir.mkdir(parents=True, exist_ok=True)

    # Create local new file
    new_local = sync_dir / "new_paper.pdf"
    new_local.write_bytes(b"%PDF-1.5 local content")

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    res = syncer.sync_pass(client=mock_quaderno_client)

    assert "new_paper.pdf" in res.pushed
    mock_quaderno_client.upload_document_sync.assert_called()


def test_syncer_deletion_propagation(tmp_path, mock_quaderno_client):
    """Verify propagating local deletion to Quaderno if previously synced."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    
    # First sync pass pulls files
    syncer.sync_pass(client=mock_quaderno_client)
    assert (sync_dir / "control_systems.pdf").exists()

    # Delete local file
    (sync_dir / "control_systems.pdf").unlink()

    # Second sync pass propagates deletion
    res = syncer.sync_pass(client=mock_quaderno_client)
    assert "control_systems.pdf" in res.deleted
    mock_quaderno_client.delete_document.assert_called_with("doc-1")


def test_syncer_push_nested_subfolder(tmp_path, mock_quaderno_client):
    """Verify pushing a file in a new local subfolder creates the remote folder first."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"
    nested_dir = sync_dir / "Research" / "Papers"
    nested_dir.mkdir(parents=True, exist_ok=True)

    new_file = nested_dir / "transformer.pdf"
    new_file.write_bytes(b"%PDF-1.5 transformer content")

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    res = syncer.sync_pass(client=mock_quaderno_client)

    assert "Research/Papers/transformer.pdf" in res.pushed
    mock_quaderno_client.create_folder_sync.assert_any_call("Document/Research")
    mock_quaderno_client.create_folder_sync.assert_any_call("Document/Research/Papers")
    mock_quaderno_client.upload_document_sync.assert_called()


def test_syncer_push_updated_local_document(tmp_path, mock_quaderno_client):
    """Verify uploading an updated local file when remote is unchanged."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    # Pull initially
    syncer.sync_pass(client=mock_quaderno_client)

    # Modify local file
    local_file = sync_dir / "control_systems.pdf"
    local_file.write_bytes(b"%PDF-1.5 modified locally")

    res = syncer.sync_pass(client=mock_quaderno_client)
    assert "control_systems.pdf" in res.pushed


def test_syncer_runner_lifecycle(tmp_path):
    """Verify sync runner start and stop lifecycle."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    runner = QuadernoSyncRunner(syncer=syncer, interval=1)

    assert runner.is_running is False
    runner.start()
    assert runner.is_running is True
    runner.stop()
    assert runner.is_running is False


def test_syncer_folder_deletion_propagation(tmp_path, mock_quaderno_client):
    """Verify deleting a local folder propagates deletion to Quaderno."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    # First sync pass pulls files and folders
    syncer.sync_pass(client=mock_quaderno_client)
    companion_dir = sync_dir / "Companion"
    assert companion_dir.exists()

    # Delete local file and folder
    (companion_dir / "quick_note.pdf").unlink()
    companion_dir.rmdir()

    res = syncer.sync_pass(client=mock_quaderno_client)
    assert "Companion/quick_note.pdf" in res.deleted
    mock_quaderno_client.delete_folder_sync.assert_called_with("Document/Companion")


def test_syncer_timestamp_preservation(tmp_path, mock_quaderno_client):
    """Verify file modification timestamp is preserved upon download."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    syncer.sync_pass(client=mock_quaderno_client)

    file_path = sync_dir / "control_systems.pdf"
    assert file_path.exists()
    # 2026-08-16T10:00:00Z timestamp
    from datetime import datetime
    expected_ts = datetime.fromisoformat("2026-08-16T10:00:00+00:00").timestamp()
    assert abs(file_path.stat().st_mtime - expected_ts) < 2.0


def test_syncer_get_known_folders(tmp_path, mock_quaderno_client):
    """Verify get_known_folders aggregates folders from client and local mirror."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"
    (sync_dir / "Research").mkdir(parents=True, exist_ok=True)

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    mock_quaderno_client.list_folders_sync.return_value = ["Document", "Document/Notes"]

    folders = syncer.get_known_folders(client=mock_quaderno_client)
    assert "Document" in folders
    assert "Document/Notes" in folders
    assert "Document/Research" in folders


def test_resolve_destination_folder_default_to_document():
    """Verify _resolve_destination_folder defaults to Document (Quaderno root)."""
    from quaderno_companion.cli import _resolve_destination_folder

    # Non-interactive session defaults to Document
    with patch("sys.stdin.isatty", return_value=False):
        dest = _resolve_destination_folder(explicit_dest=None)
        assert dest == "Document"

    # Interactive session with choice '1' (default item) selects Document
    with patch("sys.stdin.isatty", return_value=True), \
         patch("rich.prompt.Prompt.ask", return_value="1"):
        dest = _resolve_destination_folder(explicit_dest=None)
        assert dest == "Document"


def test_norm_remote_path_traversal():
    """Verify _norm_remote_path sanitizes and blocks path traversal attempts."""
    from quaderno_companion.fs.syncer import _norm_remote_path

    assert _norm_remote_path("Document/../../etc/passwd") == ""
    assert _norm_remote_path("../../.ssh/id_rsa") == ""
    assert _norm_remote_path("Document/folder/../evil.pdf") == "evil.pdf"
    assert _norm_remote_path("Document/folder/sub/safe.pdf") == "folder/sub/safe.pdf"
    assert _norm_remote_path(None) == ""
    assert _norm_remote_path("Document") == ""


def test_syncer_path_traversal_prevention(tmp_path, mock_quaderno_client):
    """Verify syncer rejects any remote entries attempting to write outside sync_dir."""
    sync_dir = tmp_path / "sync_folder"
    state_file = tmp_path / "state.json"
    outside_file = tmp_path / "should_not_exist.pdf"

    # Craft mock client returning path traversal entry paths
    mock_quaderno_client.list_all_documents.return_value = [
        {"entry_id": "root", "entry_name": "Document", "entry_path": "Document", "entry_type": "folder"},
        {"entry_id": "doc-evil-1", "entry_name": "passwd", "entry_path": "Document/../../etc/passwd", "entry_type": "document", "file_size": 100, "modified_date": "2026-08-16T10:00:00Z"},
        {"entry_id": "doc-evil-2", "entry_name": "outside.pdf", "entry_path": "../../should_not_exist.pdf", "entry_type": "document", "file_size": 100, "modified_date": "2026-08-16T10:00:00Z"},
        {"entry_id": "f-evil", "entry_name": "escape", "entry_path": "Document/../../escaped_dir", "entry_type": "folder"},
    ]

    syncer = QuadernoSyncer(sync_dir=sync_dir, state_path=state_file)
    res = syncer.sync_pass(client=mock_quaderno_client)

    # Neither evil file should be pulled or written
    assert not outside_file.exists()
    assert not (tmp_path / "escaped_dir").exists()
    assert len(res.pulled) == 0

