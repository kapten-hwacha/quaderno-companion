"""Tests for Agent Core intent routing and tool execution."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from quaderno_companion.agent.core import QuadernoAgent


@pytest.mark.asyncio
async def test_agent_navigation_intents():
    """Verify deterministic intent routing for navigation commands."""
    agent_instance = QuadernoAgent()

    with patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map:
        mock_nav = AsyncMock(return_value={"status": "success", "page": 2})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_nav if k == "navigate_reader" else None

        # Next
        res_next = await agent_instance.execute_instruction("next page")
        mock_nav.assert_called_with(action="next")

        # Prev
        res_prev = await agent_instance.execute_instruction("back")
        mock_nav.assert_called_with(action="prev")

        # Goto
        res_goto = await agent_instance.execute_instruction("jump to 12")
        mock_nav.assert_called_with(action="goto", page=12)


@pytest.mark.asyncio
async def test_agent_push_intent():
    """Verify URL detection dispatches push_document tool."""
    agent_instance = QuadernoAgent()

    with patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map:
        mock_push = AsyncMock(return_value={"status": "success"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_push if k == "push_document" else None

        res = await agent_instance.execute_instruction("Please push https://arxiv.org/abs/2301.12345 to my device")
        mock_push.assert_called_with(source_url_or_path="https://arxiv.org/abs/2301.12345")


@pytest.mark.asyncio
async def test_agent_status_intent():
    """Verify status commands invoke get_reading_state tool."""
    agent_instance = QuadernoAgent()

    with patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map:
        mock_status = AsyncMock(return_value={"status": "success", "is_connected": True})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_status if k == "get_reading_state" else None

        res = await agent_instance.execute_instruction("battery")
        mock_status.assert_called()

        res_where = await agent_instance.execute_instruction("where am i")
        assert mock_status.call_count == 2


@pytest.mark.asyncio
async def test_agent_summarize_url_intent():
    """Verify summarize intent with URL routes to summarize_and_push."""
    agent_instance = QuadernoAgent()

    with patch.object(agent_instance, "summarize_and_push", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = {"status": "success"}
        await agent_instance.execute_instruction("summarize https://example.com/paper.pdf")
        mock_sum.assert_called_with("https://example.com/paper.pdf")


@pytest.mark.asyncio
async def test_agent_fallback_heuristic():
    """Verify unrecognized instruction returns informative status."""
    agent_instance = QuadernoAgent()
    res = await agent_instance.execute_instruction("make me a coffee")
    assert res["status"] == "unrecognized"
    assert "Could not determine Quaderno action" in res["message"]


@pytest.mark.asyncio
async def test_agent_summarize_pages_parameter():
    """Verify summarize_and_push passes target pages to tool_summarize_to_eink."""
    agent_instance = QuadernoAgent()

    with patch.object(agent_instance, "_generate_gemini_summary", new_callable=AsyncMock) as mock_gemini, \
         patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map, \
         patch("quaderno_companion.agent.core.settings") as mock_settings:

        mock_settings.gemini_api_key = "fake-key"
        mock_gemini.return_value = {
            "title": "3-Page Test Doc",
            "key_takeaways": ["Point 1", "Point 2", "Point 3", "Point 4"],
            "sections": {"Section 1": "Overview", "Section 2": "Analysis"},
        }
        mock_sum_tool = AsyncMock(return_value={"status": "success", "message": "Pushed"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_sum_tool if k == "summarize_to_eink" else None

        sample_text = "Sample article content for multi-page synthesis test."

        res = await agent_instance.summarize_and_push(
            text_or_url=sample_text,
            title="Test Doc",
            pages=3,
            provider="gemini_api",
        )
        assert res["status"] == "success"
        mock_sum_tool.assert_called_once()
        call_kwargs = mock_sum_tool.call_args.kwargs
        assert call_kwargs["pages"] == 3
        assert "3-Page Test Doc" in call_kwargs["title"]
        assert len(call_kwargs["key_takeaways"]) == 4


@pytest.mark.asyncio
async def test_agent_summarize_gemini_notebook():
    """Verify QuadernoAgent calls GeminiNotebookClient when provider is gemini_notebook."""
    mock_nb_client = MagicMock()
    mock_nb_client.is_available.return_value = True
    mock_nb_client.is_authenticated.return_value = True
    mock_nb_client.generate_summary = AsyncMock(return_value={
        "title": "Synthesized AI Brief",
        "key_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        "sections": {"Executive Summary": "Structured text from NotebookLM."},
    })

    agent_instance = QuadernoAgent(notebook_client=mock_nb_client)

    with patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map:
        mock_sum_tool = AsyncMock(return_value={"status": "success", "message": "Pushed"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_sum_tool if k == "summarize_to_eink" else None

        res = await agent_instance.summarize_and_push(
            text_or_url="Content to summarize",
            title="Custom Title",
            pages=2,
            notebook_url="https://notebooklm.google.com/notebook/123",
            provider="gemini_notebook",
        )

        assert res["status"] == "success"
        mock_nb_client.generate_summary.assert_called_once_with(
            text_or_content="Content to summarize",
            title="Custom Title",
            pages=2,
            notebook_url="https://notebooklm.google.com/notebook/123",
            notebook_id=None,
            mode=None,
            cleanup=None,
            source_file=None,
            source_url=None,
        )
        mock_sum_tool.assert_called_once()
        call_kwargs = mock_sum_tool.call_args.kwargs
        assert call_kwargs["title"] == "Synthesized AI Brief"
        assert call_kwargs["key_takeaways"] == ["Takeaway 1", "Takeaway 2", "Takeaway 3"]


@pytest.mark.asyncio
async def test_agent_summarize_notebook_fallback_to_gemini_api():
    """Verify QuadernoAgent falls back to Gemini API if Gemini Notebook fails."""
    mock_nb_client = MagicMock()
    mock_nb_client.is_available.return_value = True
    mock_nb_client.is_authenticated.return_value = True
    mock_nb_client.generate_summary = AsyncMock(side_effect=RuntimeError("Browser crashed"))

    agent_instance = QuadernoAgent(notebook_client=mock_nb_client)

    with patch.object(agent_instance, "_generate_gemini_summary", new_callable=AsyncMock) as mock_gemini, \
         patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map, \
         patch("quaderno_companion.agent.core.settings") as mock_settings:

        mock_settings.gemini_api_key = "fake-key"
        mock_gemini.return_value = {
            "title": "Fallback Gemini Synthesis",
            "key_takeaways": ["Point A", "Point B"],
            "sections": {"Summary": "Details"},
        }
        mock_sum_tool = AsyncMock(return_value={"status": "success", "message": "Pushed"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_sum_tool if k == "summarize_to_eink" else None

        res = await agent_instance.summarize_and_push(
            text_or_url="Paragraph 1 is long enough.\n\nParagraph 2 is detailed.",
            title="Fallback Title",
            pages=1,
            provider="gemini_notebook",
        )

        assert res["status"] == "success"
        mock_gemini.assert_called_once()
        mock_sum_tool.assert_called_once()
        call_kwargs = mock_sum_tool.call_args.kwargs
        assert "Fallback Gemini Synthesis" in call_kwargs["title"]


@pytest.mark.asyncio
async def test_agent_summarize_ephemeral_full_pipeline():
    """Verify full summarization pipeline: agent -> notebook -> E-ink PDF generation -> device manager."""
    mock_nb_client = MagicMock()
    mock_nb_client.is_available.return_value = True
    mock_nb_client.is_authenticated.return_value = True
    mock_nb_client.generate_summary = AsyncMock(return_value={
        "title": "Quantum Computing 2026",
        "key_takeaways": [
            "Hardware milestone reached",
            "Superconducting coherence extended",
            "Zero error rate observed",
        ],
        "sections": {
            "Executive Overview": "A landmark achievement in quantum hardware.",
            "Technical Breakthrough": "Coherence times reached 10 milliseconds.",
        },
    })

    agent_instance = QuadernoAgent(notebook_client=mock_nb_client)

    with patch("quaderno_companion.agent.tools.device_manager.open_document", new_callable=AsyncMock) as mock_open:
        mock_open.return_value = {"status": "success", "document_id": "doc-123"}

        res = await agent_instance.summarize_and_push(
            text_or_url="Some long article content about quantum computing...",
            title="Quantum Article",
            pages=2,
            provider="gemini_notebook",
            notebook_mode="ephemeral",
            cleanup=True,
        )

        assert res["status"] == "success"
        mock_open.assert_called_once()
        call_kwargs = mock_open.call_args.kwargs
        assert "Quantum Computing 2026" in call_kwargs["title"]
        pdf_bytes = call_kwargs["pdf_bytes"]
        assert pdf_bytes.startswith(b"%PDF-1.")


@pytest.mark.asyncio
async def test_agent_summarize_gemini_api():
    """Verify QuadernoAgent calls _generate_gemini_summary when provider is gemini_api."""
    agent_instance = QuadernoAgent()

    with patch.object(agent_instance, "_generate_gemini_summary", new_callable=AsyncMock) as mock_gemini, \
         patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map, \
         patch("quaderno_companion.agent.core.settings") as mock_settings:

        mock_settings.gemini_api_key = "fake-key"
        mock_settings.llm_model = "gemini-2.5-flash"
        mock_gemini.return_value = {
            "title": "Gemini 2.5 Flash Synthesis",
            "key_takeaways": ["Point 1", "Point 2", "Point 3"],
            "sections": {"Summary": "Fast API generation."},
        }

        mock_sum_tool = AsyncMock(return_value={"status": "success", "message": "Pushed"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_sum_tool if k == "summarize_to_eink" else None

        res = await agent_instance.summarize_and_push(
            text_or_url="Article content for Gemini API...",
            title="Custom Gemini Title",
            pages=1,
            provider="gemini_api",
        )

        assert res["status"] == "success"
        mock_gemini.assert_called_once()
        mock_sum_tool.assert_called_once()
        call_kwargs = mock_sum_tool.call_args.kwargs
        assert call_kwargs["title"] == "Gemini 2.5 Flash Synthesis"
        assert call_kwargs["key_takeaways"] == ["Point 1", "Point 2", "Point 3"]






