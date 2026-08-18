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

        app._execute_push_or_summarize(target="https://example.com/test", title="Test Page")
        # Wait briefly for worker thread
        import time
        time.sleep(0.1)

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

        app._execute_push_or_summarize(target="https://example.com/test", title="Test Page")
        import time
        time.sleep(0.1)

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

        app._execute_push_or_summarize(target="https://example.com/test", title="Test Page", page=2)
        import time
        time.sleep(0.1)

        mock_push.assert_called_once_with(source_url_or_path="https://example.com/test", title="Test Page", page=2)
