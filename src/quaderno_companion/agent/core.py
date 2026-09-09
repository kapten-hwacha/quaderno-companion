"""Autonomous Agent Core and Intent Execution Engine.

Dispatches natural language instructions and programmatic triggers to Quaderno tools.
Supports zero-latency deterministic intent matching for reading navigation and document push.
"""

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from quaderno_companion.agent.prompts import AGENT_SYSTEM_PROMPT
from quaderno_companion.agent.tools import (
    TOOL_DEFINITIONS,
    TOOL_MAP,
    tool_get_reading_state,
    tool_navigate_reader,
    tool_push_document,
)
from quaderno_companion.config import settings

logger = logging.getLogger(__name__)


class QuadernoAgent:
    """Agent orchestrator for Quaderno E-ink bridge."""

    def __init__(self):
        self.system_prompt = AGENT_SYSTEM_PROMPT

    async def execute_instruction(self, query: str) -> Dict[str, Any]:
        """Parse instruction and execute appropriate Quaderno tool."""
        # 1. Check for deterministic fast-path intents (navigation, status, direct URL)
        direct_result = await self._try_deterministic_intent(query)
        if direct_result:
            return dict(direct_result)

        # 2. Fallback: return an informative unrecognized message
        return await self._fallback_heuristic(query)

    async def _call_tool(self, tool_name: str, **kwargs) -> Any:
        """Helper to invoke a registered tool handler dynamically."""
        handler = TOOL_MAP[tool_name]
        return await handler(**kwargs)

    async def _try_deterministic_intent(self, query: str) -> Optional[Dict[str, Any]]:
        """Fast regex matcher for common navigation and push commands."""
        clean = query.strip().lower()

        # Next page
        if clean in ("next", "next page", "forward", "n", "page down", "j"):
            return dict(await self._call_tool("navigate_reader", action="next"))

        # Previous page
        if clean in ("prev", "previous", "previous page", "back", "p", "page up", "k"):
            return dict(await self._call_tool("navigate_reader", action="prev"))

        # Goto page N
        goto_match = re.match(r"(?:goto|page|jump to|go to)\s+(\d+)", clean)
        if goto_match:
            page_num = int(goto_match.group(1))
            return dict(await self._call_tool("navigate_reader", action="goto", page=page_num))

        # Status
        if clean in ("status", "state", "reading status", "where am i", "battery"):
            return dict(await self._call_tool("get_reading_state"))

        # Push direct URL / file
        url_match = re.search(r"(https?://[^\s]+)", query)
        if url_match:
            url = url_match.group(1)
            return dict(await self._call_tool("push_document", source_url_or_path=url))

        return None

    async def _fallback_heuristic(self, query: str) -> Dict[str, Any]:
        """Fallback when no direct regex matched."""
        return {
            "status": "unrecognized",
            "message": f"Could not determine Quaderno action for query: '{query}'. Try 'next', 'prev', 'goto <page>', or provide a URL/file path.",
        }


# Global agent instance
agent = QuadernoAgent()

