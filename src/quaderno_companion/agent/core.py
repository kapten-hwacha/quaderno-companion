"""Autonomous Agent Core and Intent Execution Engine.

Dispatches natural language instructions and programmatic triggers to Quaderno tools.
Supports both zero-latency deterministic intent matching and LLM tool calling.
"""

import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import httpx

from quaderno_companion.agent.prompts import AGENT_SYSTEM_PROMPT, SUMMARIZE_PROMPT
from quaderno_companion.agent.tools import (
    TOOL_DEFINITIONS,
    TOOL_MAP,
    tool_get_reading_state,
    tool_navigate_reader,
    tool_push_document,
    tool_summarize_to_eink,
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
            # Check if user requested summary
            if "summarize" in clean or "summary" in clean:
                return await self.summarize_and_push(url)
            else:
                return dict(await self._call_tool("push_document", source_url_or_path=url))

        return None

    async def summarize_and_push(
        self,
        text_or_url: str,
        title: Optional[str] = None,
        pages: int = 1,
    ) -> Dict[str, Any]:
        """Summarize content and push executive brief to Quaderno."""
        content_text = text_or_url
        target_pages = max(1, min(5, pages))
        
        # 1. Check if local file
        if "\n" not in text_or_url and len(text_or_url) < 1024:
            try:
                local_path = Path(text_or_url).expanduser().resolve()
                if local_path.is_file():
                    title = title or local_path.stem
                    if local_path.suffix.lower() == ".pdf":
                        import pymupdf as fitz
                        doc = fitz.open(local_path)
                        pages_text = [page.get_text() for page in doc[: max(10, target_pages * 5)]]
                        content_text = "\n\n".join(pages_text)
                        doc.close()
                    else:
                        content_text = local_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                pass

        # 2. Fetch text if remote URL
        elif text_or_url.startswith("http"):
            # If ArXiv URL, fetch metadata or abstract
            arxiv_match = re.search(r"arxiv\.org/(?:abs|html|pdf)/([0-9]+\.[0-9]+(?:v[0-9]+)?)", text_or_url)
            if arxiv_match:
                arxiv_id = arxiv_match.group(1)
                abs_url = f"https://arxiv.org/abs/{arxiv_id}"
                async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                    resp = await client.get(abs_url)
                    if resp.is_success:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(resp.text, "html.parser")
                        title_el = soup.find("h1", class_="title")
                        title = title_el.get_text().replace("Title:", "").strip() if title_el else f"ArXiv {arxiv_id}"
                        abstract_el = soup.find("blockquote", class_="abstract")
                        content_text = abstract_el.get_text().replace("Abstract:", "").strip() if abstract_el else resp.text
            else:
                async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                    resp = await client.get(text_or_url)
                    if resp.is_success:
                        from readability import Document
                        from bs4 import BeautifulSoup
                        doc = Document(resp.text)
                        title = title or doc.title()
                        soup = BeautifulSoup(doc.summary(), "html.parser")
                        content_text = soup.get_text(separator="\n").strip()

        # Synthesize takeaways
        takeaways = [
            f"Synthesized from {title or 'Source'}",
            f"High-contrast E-ink executive brief ({target_pages} page{'s' if target_pages > 1 else ''}).",
        ]
        
        # If Gemini key is set, generate rich structured summary via LLM
        if settings.gemini_api_key:
            try:
                llm_summary = await self._generate_gemini_summary(content_text, pages=target_pages)
                if llm_summary:
                    return dict(await self._call_tool(
                        "summarize_to_eink",
                        text_or_url=text_or_url,
                        title=llm_summary.get("title", title),
                        key_takeaways=llm_summary.get("key_takeaways", takeaways),
                        sections=llm_summary.get("sections"),
                        pages=target_pages,
                    ))
            except Exception as e:
                logger.warning(f"LLM summary generation failed, using rule-based summary: {e}")

        # Rule-based fallback summary scaled to target_pages
        paragraphs = [p.strip() for p in content_text.split("\n") if len(p.strip()) > 30]
        if paragraphs:
            takeaways = [p[:120] + "..." for p in paragraphs[: min(len(paragraphs), 3 + target_pages)]]
            sections = {}
            if target_pages == 1:
                sections["Executive Summary"] = "\n\n".join(paragraphs[4:8]) if len(paragraphs) > 4 else paragraphs[0]
            else:
                chunk_size = 4
                available_paras = paragraphs[4:] if len(paragraphs) > 4 else paragraphs
                for i in range(target_pages):
                    start = i * chunk_size
                    end = start + chunk_size
                    chunk = available_paras[start:end]
                    if chunk:
                        sec_name = f"Section {i + 1}: Overview" if i == 0 else f"Section {i + 1}: Detailed Analysis"
                        sections[sec_name] = "\n\n".join(chunk)
                if not sections:
                    sections["Executive Summary"] = paragraphs[0]
        else:
            sections = {"Overview": content_text[: 1000 * target_pages]}

        return dict(await self._call_tool(
            "summarize_to_eink",
            text_or_url=text_or_url,
            title=title or "Document Brief",
            key_takeaways=takeaways,
            sections=sections,
            pages=target_pages,
        ))

    async def _generate_gemini_summary(self, text: str, pages: int = 1) -> Optional[Dict[str, Any]]:
        """Call Gemini API for structured JSON summary."""
        if not settings.gemini_api_key:
            return None

        prompt = SUMMARIZE_PROMPT.format(content=text[:12000], pages=pages)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.llm_model}:generateContent"
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        headers = {
            "x-goog-api-key": settings.gemini_api_key,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)


    async def _fallback_heuristic(self, query: str) -> Dict[str, Any]:
        """Fallback when no direct regex matched."""
        return {
            "status": "unrecognized",
            "message": f"Could not determine Quaderno action for query: '{query}'. Try 'next', 'prev', 'goto <page>', or provide a URL/file path.",
        }


# Global agent instance
agent = QuadernoAgent()
