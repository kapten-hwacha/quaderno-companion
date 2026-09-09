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
async def test_agent_fallback_heuristic():
    """Verify unrecognized instruction returns informative status."""
    agent_instance = QuadernoAgent()
    res = await agent_instance.execute_instruction("make me a coffee")
    assert res["status"] == "unrecognized"
    assert "Could not determine Quaderno action" in res["message"]







