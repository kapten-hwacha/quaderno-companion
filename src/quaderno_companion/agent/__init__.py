"""Agent core, tools, and prompts for Quaderno Companion."""

from quaderno_companion.agent.core import QuadernoAgent, agent
from quaderno_companion.agent.prompts import AGENT_SYSTEM_PROMPT, SUMMARIZE_PROMPT
from quaderno_companion.agent.tools import (
    TOOL_DEFINITIONS,
    TOOL_MAP,
    tool_get_reading_state,
    tool_navigate_reader,
    tool_push_document,
    tool_summarize_to_eink,
)

__all__ = [
    "QuadernoAgent",
    "agent",
    "AGENT_SYSTEM_PROMPT",
    "SUMMARIZE_PROMPT",
    "TOOL_DEFINITIONS",
    "TOOL_MAP",
    "tool_push_document",
    "tool_navigate_reader",
    "tool_get_reading_state",
    "tool_summarize_to_eink",
]
