"""Tests for Menubar App summary slider and interaction logic."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quaderno_companion.triggers.menubar import QuadernoMenubarApp


def test_menubar_summary_pages_property():
    """Verify summary_pages getter, setter, clamping, and cycling."""
    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    def _get_displayed_text():
        badge = getattr(app, "summary_badge", None)
        if badge is not None:
            return badge.stringValue()
        return app.summary_slider_item.title

    # Initial state should be 0 (Off)
    assert app.summary_pages == 0
    assert "Off" in _get_displayed_text()
    assert "Summary" in app.summary_slider_item.title
    if getattr(app, "summary_label", None) is not None:
        assert "Summary" in app.summary_label.stringValue()

    # Set to 2 pages
    app.summary_pages = 2
    assert app.summary_pages == 2
    assert "2 pgs" in _get_displayed_text()
    assert "Summary" in app.summary_slider_item.title

    # Set to 1 page
    app.summary_pages = 1
    assert app.summary_pages == 1
    assert "1 pg" in _get_displayed_text()
    assert "Summary" in app.summary_slider_item.title

    # Clamping test (exceeding max 5)
    app.summary_pages = 10
    assert app.summary_pages == 5

    # Clamping test (below min 0)
    app.summary_pages = -3
    assert app.summary_pages == 0

    # Test cycling: 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 0
    app.summary_pages = 0
    app.cycle_summary_pages()
    assert app.summary_pages == 1
    app.cycle_summary_pages()
    assert app.summary_pages == 2
    app.summary_pages = 5
    app.cycle_summary_pages()
    assert app.summary_pages == 0


def test_menubar_summarizer_provider_property():
    """Verify summarizer_provider getter, setter, and toggle."""
    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    # Initial state should match settings or default to gemini_api
    app.summarizer_provider = "gemini_api"
    assert app.summarizer_provider == "gemini_api"

    # Toggle to notebooklm
    app.toggle_summarizer_provider()
    assert app.summarizer_provider == "gemini_notebook"

    # Toggle back to api
    app.toggle_summarizer_provider()
    assert app.summarizer_provider == "gemini_api"

    # Direct setter
    app.summarizer_provider = "notebooklm"
    assert app.summarizer_provider == "gemini_notebook"


def test_menubar_execute_push_or_summarize_routing():
    """Verify _execute_push_or_summarize calls agent.summarize_and_push with active provider."""
    with patch("rumps.Timer"):
        app = QuadernoMenubarApp()

    # Case 1: summary_pages = 3, provider = gemini_notebook
    app.summary_pages = 3
    app.summarizer_provider = "gemini_notebook"
    with patch("quaderno_companion.triggers.menubar.agent.summarize_and_push", new_callable=AsyncMock) as mock_sum, \
         patch("quaderno_companion.triggers.menubar.notify") as mock_notify:
        mock_sum.return_value = {"status": "success", "message": "Pushed 3-page summary"}

        fut = app._execute_push_or_summarize(target="https://example.com/test", title="Test Page")
        if fut is not None:
            fut.result(timeout=5.0)

        mock_sum.assert_called_once_with(
            text_or_url="https://example.com/test",
            title="Test Page",
            pages=3,
            provider="gemini_notebook",
        )

    # Case 2: summary_pages = 2, provider = gemini_api
    app.summary_pages = 2
    app.summarizer_provider = "gemini_api"
    with patch("quaderno_companion.triggers.menubar.agent.summarize_and_push", new_callable=AsyncMock) as mock_sum, \
         patch("quaderno_companion.triggers.menubar.notify") as mock_notify:
        mock_sum.return_value = {"status": "success", "message": "Pushed 2-page summary"}

        fut = app._execute_push_or_summarize(target="https://example.com/test", title="Test Page")
        if fut is not None:
            fut.result(timeout=5.0)

        mock_sum.assert_called_once_with(
            text_or_url="https://example.com/test",
            title="Test Page",
            pages=2,
            provider="gemini_api",
        )

    # Case 3: summary_pages = 0 -> calls tool_push_document (direct push)
    app.summary_pages = 0
    with patch("quaderno_companion.triggers.menubar.tool_push_document", new_callable=AsyncMock) as mock_push, \
         patch("quaderno_companion.triggers.menubar.notify") as mock_notify:
        mock_push.return_value = {"status": "success", "message": "Pushed document"}

        fut = app._execute_push_or_summarize(target="https://example.com/test", title="Test Page", page=2)
        if fut is not None:
            fut.result(timeout=5.0)

        mock_push.assert_called_once_with(source_url_or_path="https://example.com/test", title="Test Page", page=2)


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



