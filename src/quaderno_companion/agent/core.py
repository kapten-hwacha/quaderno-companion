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
from quaderno_companion.pipeline.notebook_client import GeminiNotebookClient

logger = logging.getLogger(__name__)


class QuadernoAgent:
    """Agent orchestrator for Quaderno E-ink bridge."""

    def __init__(self, notebook_client: Optional[GeminiNotebookClient] = None):
        self.system_prompt = AGENT_SYSTEM_PROMPT
        self.notebook_client = notebook_client or GeminiNotebookClient()

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
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None,
        provider: Optional[str] = None,
        notebook_mode: Optional[str] = None,
        cleanup: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Summarize content and push executive brief to Quaderno."""
        content_text = text_or_url.replace("\x00", "") if isinstance(text_or_url, str) else text_or_url
        target_pages = max(1, min(5, pages))
        active_provider = provider or settings.summarizer_provider or "gemini_notebook"
        source_file = None
        source_url = None
        if title:
            title = title.replace("\x00", "")
        
        # 1. Check if local file
        if "\n" not in text_or_url and len(text_or_url) < 1024:
            try:
                local_path = Path(text_or_url).expanduser().resolve()
                if local_path.is_file():
                    source_file = str(local_path)
                    title = title or local_path.stem
                    if local_path.suffix.lower() == ".pdf":
                        import pymupdf as fitz
                        doc = fitz.open(local_path)
                        pages_text = [page.get_text().replace("\x00", "") for page in doc[: max(10, target_pages * 5)]]
                        content_text = "\n\n".join(pages_text)
                        doc.close()
                    else:
                        content_text = local_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "")
            except (OSError, ValueError):
                pass

        # 2. Fetch text if remote URL
        elif text_or_url.startswith("http"):
            source_url = text_or_url
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

        # Synthesize default takeaways
        takeaways = [
            f"Synthesized from {title or 'Source'}",
            f"High-contrast E-ink executive brief ({target_pages} page{'s' if target_pages > 1 else ''}).",
        ]

        # 3. Direct Gemini API
        if active_provider in ("gemini_api", "llm", "google_genai") or (
            active_provider == "auto" and settings.gemini_api_key
        ):
            if settings.gemini_api_key:
                try:
                    logger.info(f"Synthesizing summary via Gemini API ({settings.llm_model})...")
                    llm_summary = await self._generate_gemini_summary(content_text, pages=target_pages)
                    if llm_summary:
                        return dict(await self._call_tool(
                            "summarize_to_eink",
                            text_or_url=text_or_url,
                            title=llm_summary.get("title") or title or "Executive Brief",
                            key_takeaways=llm_summary.get("key_takeaways", takeaways),
                            sections=llm_summary.get("sections"),
                            pages=target_pages,
                        ))
                except Exception as e:
                    logger.warning(f"Gemini API summary generation failed: {e}")

        # 4. Gemini Notebook (NotebookLM) summarizer
        if active_provider in ("gemini_notebook", "notebooklm", "auto", "gemini_api"):
            try:
                if self.notebook_client.is_available() and self.notebook_client.is_authenticated():
                    logger.info("Synthesizing summary via Gemini Notebook (NotebookLM)...")
                    nb_summary = await self.notebook_client.generate_summary(
                        text_or_content=content_text,
                        title=title,
                        pages=target_pages,
                        notebook_url=notebook_url,
                        notebook_id=notebook_id,
                        mode=notebook_mode,
                        cleanup=cleanup,
                        source_file=source_file,
                        source_url=source_url,
                    )
                    if nb_summary:
                        return dict(await self._call_tool(
                            "summarize_to_eink",
                            text_or_url=text_or_url,
                            title=nb_summary.get("title") or title or "Executive Brief",
                            key_takeaways=nb_summary.get("key_takeaways", takeaways),
                            sections=nb_summary.get("sections"),
                            pages=target_pages,
                        ))
            except Exception as e:
                logger.warning(f"Gemini Notebook summarization failed: {e}")

        # 5. Gemini API fallback if NotebookLM was primary and failed
        if active_provider in ("gemini_notebook", "notebooklm") and settings.gemini_api_key:
            try:
                logger.info(f"Falling back to Gemini API ({settings.llm_model})...")
                llm_summary = await self._generate_gemini_summary(content_text, pages=target_pages)
                if llm_summary:
                    return dict(await self._call_tool(
                        "summarize_to_eink",
                        text_or_url=text_or_url,
                        title=llm_summary.get("title") or title or "Executive Brief",
                        key_takeaways=llm_summary.get("key_takeaways", takeaways),
                        sections=llm_summary.get("sections"),
                        pages=target_pages,
                    ))
            except Exception as e:
                logger.warning(f"Fallback Gemini API summary generation failed: {e}")

        # 6. If no LLM synthesis succeeded, raise clear error
        raise RuntimeError(
            "Summarization failed: No LLM synthesis provider succeeded. "
            "Please ensure GEMINI_API_KEY is set in your environment/.env, "
            "or authenticate Gemini Notebook with `quadctl notebook login`."
        )

    async def _generate_gemini_summary(self, text: str, pages: int = 1) -> Optional[Dict[str, Any]]:
        """Call Gemini API for structured JSON summary."""
        if not settings.gemini_api_key:
            return None

        from quaderno_companion.agent.prompts import get_page_length_instruction
        prompt = SUMMARIZE_PROMPT.format(
            content=text[:25000],
            length_instruction=get_page_length_instruction(pages),
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.llm_model}:generateContent"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        headers = {
            "x-goog-api-key": settings.gemini_api_key,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            clean_json = raw_text.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            return json.loads(clean_json.strip())


    async def _fallback_heuristic(self, query: str) -> Dict[str, Any]:
        """Fallback when no direct regex matched."""
        return {
            "status": "unrecognized",
            "message": f"Could not determine Quaderno action for query: '{query}'. Try 'next', 'prev', 'goto <page>', or provide a URL/file path.",
        }


# Global agent instance
agent = QuadernoAgent()
