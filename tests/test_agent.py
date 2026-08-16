"""Tests for Agent Core intent routing and tool execution."""

from unittest.mock import AsyncMock, patch
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

    with patch("quaderno_companion.agent.core.TOOL_MAP") as mock_tool_map:
        mock_sum_tool = AsyncMock(return_value={"status": "success", "message": "Pushed"})
        mock_tool_map.__getitem__.side_effect = lambda k: mock_sum_tool if k == "summarize_to_eink" else None

        sample_text = (
            "Paragraph one is introductory.\n\n"
            "Paragraph two explains background details.\n\n"
            "Paragraph three describes the core architecture.\n\n"
            "Paragraph four discusses performance results.\n\n"
            "Paragraph five goes into deeper technical metrics and benchmark scores.\n\n"
            "Paragraph six provides analysis on power consumption and ergonomics.\n\n"
            "Paragraph seven outlines system limitations and constraints.\n\n"
            "Paragraph eight provides comprehensive future research directions.\n\n"
        )

        res = await agent_instance.summarize_and_push(text_or_url=sample_text, title="Test Doc", pages=3)
        assert res["status"] == "success"
        mock_sum_tool.assert_called_once()
        call_kwargs = mock_sum_tool.call_args.kwargs
        assert call_kwargs["pages"] == 3
        assert "Test Doc" in call_kwargs["title"]
        assert len(call_kwargs["key_takeaways"]) >= 3


