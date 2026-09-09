"""Tests for Menubar App interaction logic."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quaderno_companion.triggers.menubar import QuadernoMenubarApp


def test_menubar_execute_push_routing():
    """Verify _execute_push_or_summarize prompts for destination and calls tool_push_document."""
    from quaderno_companion.config import settings
    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    with patch("quaderno_companion.triggers.menubar.tool_push_document", new_callable=AsyncMock) as mock_push, \
         patch("quaderno_companion.triggers.menubar.prompt_folder_dialog", return_value="Document/Companion") as mock_prompt, \
         patch("quaderno_companion.triggers.menubar.notify") as mock_notify:
        mock_push.return_value = {"status": "success", "message": "Pushed document"}

        fut = app._execute_push_or_summarize(target="https://example.com/test", title="Test Page", page=2)
        if fut is not None:
            fut.result(timeout=5.0)

        mock_prompt.assert_called_once_with(
            title="Select Destination Folder on Quaderno",
            initial_folder="Document",
            root_mirror=settings.sync_dir,
        )

        mock_push.assert_called_once_with(
            source_url_or_path="https://example.com/test",
            title="Test Page",
            page=2,
            destination_folder="Document/Companion",
        )



def test_menubar_instant_page_navigation():
    """Verify that nav_prev and nav_next update page state and UI instantly."""
    from quaderno_companion.device.manager import ReadingState

    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    # Set up active document reading state
    state = ReadingState(
        document_id="doc-123",
        title="Sample Document",
        current_page=5,
        total_pages=10,
    )
    app._last_reading_state = state

    with patch("quaderno_companion.triggers.menubar.tool_navigate_reader", new_callable=AsyncMock) as mock_nav, \
         patch.object(app, "refresh_telemetry"):
        # Test nav_next
        app.nav_next(None)
        assert app._last_reading_state.current_page == 6
        assert "Sample Document (6/10)" in app.doc_item.title
        if getattr(app, "slider_page_badge", None) is not None:
            assert "6 / 10" in app.slider_page_badge.stringValue()

        # Test nav_prev
        app.nav_prev(None)
        assert app._last_reading_state.current_page == 5
        assert "Sample Document (5/10)" in app.doc_item.title
        if getattr(app, "slider_page_badge", None) is not None:
            assert "5 / 10" in app.slider_page_badge.stringValue()

        # Test consecutive rapid nav_next clicks
        app.nav_next(None)
        assert app._last_reading_state.current_page == 6
        app.nav_next(None)
        assert app._last_reading_state.current_page == 7
        app.nav_next(None)
        assert app._last_reading_state.current_page == 8
        assert "Sample Document (8/10)" in app.doc_item.title

        # Test consecutive rapid nav_prev clicks
        app.nav_prev(None)
        assert app._last_reading_state.current_page == 7
        app.nav_prev(None)
        assert app._last_reading_state.current_page == 6
        assert "Sample Document (6/10)" in app.doc_item.title


def test_menubar_chapters_menu_with_toc():
    """Verify refresh_telemetry populates chapters_menu from device_manager.get_toc."""
    import time
    from quaderno_companion.device.manager import DeviceStatus, ReadingState

    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    status = DeviceStatus(
        is_connected=True,
        battery_level=85,
        reading_state=ReadingState(
            document_id="doc-with-chapters",
            title="Book with TOC",
            current_page=3,
            total_pages=50,
        ),
    )

    mock_toc = [("Chapter 1: Begin", 1), ("Chapter 2: Middle", 25), ("Chapter 3: End", 45)]

    with patch.object(app, "_dispatch_to_main", side_effect=lambda f: f()), \
         patch("quaderno_companion.triggers.menubar.device_manager.get_status", new_callable=AsyncMock, return_value=status), \
         patch("quaderno_companion.triggers.menubar.device_manager.get_toc", new_callable=AsyncMock, return_value=mock_toc):

        fut = app.refresh_telemetry()
        if fut is not None:
            fut.result(timeout=5.0)

        # Check menu items in chapters_menu
        items = list(app.chapters_menu.values())
        item_titles = [it.title for it in items]

        assert any("Chapter 1: Begin" in t for t in item_titles)
        assert any("Chapter 2: Middle" in t for t in item_titles)
        assert any("Chapter 3: End" in t for t in item_titles)


def test_menubar_chapters_menu_landmark_fallback():
    """Verify refresh_telemetry falls back to landmarks when TOC is empty on multi-page doc."""
    from quaderno_companion.device.manager import DeviceStatus, ReadingState

    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    status = DeviceStatus(
        is_connected=True,
        battery_level=85,
        reading_state=ReadingState(
            document_id="doc-no-toc",
            title="Paper without TOC",
            current_page=1,
            total_pages=20,
        ),
    )

    with patch.object(app, "_dispatch_to_main", side_effect=lambda f: f()), \
         patch("quaderno_companion.triggers.menubar.device_manager.get_status", new_callable=AsyncMock, return_value=status), \
         patch("quaderno_companion.triggers.menubar.device_manager.get_toc", new_callable=AsyncMock, return_value=[]):

        fut = app.refresh_telemetry()
        if fut is not None:
            fut.result(timeout=5.0)

        items = list(app.chapters_menu.values())
        item_titles = [it.title for it in items]

        assert any("Start of Document" in t for t in item_titles)
        assert any("50% (Halfway)" in t for t in item_titles)
        assert any("End of Document" in t for t in item_titles)


def test_prompt_folder_dialog(tmp_path):
    """Verify prompt_folder_dialog resolves mirror subfolders to remote paths."""
    from unittest.mock import MagicMock
    from quaderno_companion.triggers.preview import prompt_folder_dialog

    mirror = tmp_path / "Quaderno"
    mirror.mkdir()
    sub = mirror / "Research" / "AI"
    sub.mkdir(parents=True)

    # 1. Subfolder selected
    mock_run_sub = MagicMock(return_value=MagicMock(returncode=0, stdout=str(sub) + "\n"))
    with patch("subprocess.run", mock_run_sub), patch("sys.platform", "darwin"):
        res = prompt_folder_dialog(root_mirror=mirror)
        assert res == "Document/Research/AI"

    # 2. Root mirror selected
    mock_run_root = MagicMock(return_value=MagicMock(returncode=0, stdout=str(mirror) + "\n"))
    with patch("subprocess.run", mock_run_root), patch("sys.platform", "darwin"):
        res = prompt_folder_dialog(root_mirror=mirror)
        assert res == "Document"

    # 3. User cancelled
    mock_run_cancel = MagicMock(return_value=MagicMock(returncode=1, stdout=""))
    with patch("subprocess.run", mock_run_cancel), patch("sys.platform", "darwin"):
        res = prompt_folder_dialog(root_mirror=mirror)
        assert res is None

    # 4. Outside root mirror selected -> gives error alert and returns None
    outside_dir = tmp_path / "OtherFolder"
    outside_dir.mkdir()
    mock_run_outside = MagicMock(return_value=MagicMock(returncode=0, stdout=str(outside_dir) + "\n"))
    with patch("subprocess.run", mock_run_outside), \
         patch("sys.platform", "darwin"), \
         patch("quaderno_companion.triggers.preview.show_alert") as mock_alert:
        res = prompt_folder_dialog(root_mirror=mirror)
        assert res is None
        mock_alert.assert_called_once()
        args, _ = mock_alert.call_args
        assert "Invalid Destination" in args[0]
        assert "outside the Quaderno mirror" in args[1]


def test_menubar_push_menu_structure():
    """Verify that 'Push Local File...' is the only visible push item, and other push options are in a submenu."""
    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    top_titles = [item.title for item in app.menu.values() if hasattr(item, "title")]

    # Only "📁 Push Local File..." should be visible in the top-level menu
    assert "📁 Push Local File..." in top_titles
    assert "Other Push Options" in top_titles

    # Other push options should NOT be in the top-level menu
    assert "🌐 Push Active Browser Tab" not in top_titles
    assert "🖥️ Push Active Window" not in top_titles
    assert "📋 Push from Clipboard" not in top_titles
    assert "👁️ Push from Preview" not in top_titles
    assert "🔗 Push URL..." not in top_titles

    # Verify they are present in the submenu
    sub_titles = [item.title for item in app.other_push_menu.values() if hasattr(item, "title")]
    assert "🌐 Push Active Browser Tab" in sub_titles
    assert "🖥️ Push Active Window" in sub_titles
    assert "📋 Push from Clipboard" in sub_titles
    assert "👁️ Push from Preview" in sub_titles
    assert "🔗 Push URL..." in sub_titles




