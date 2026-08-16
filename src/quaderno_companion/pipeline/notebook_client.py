"""Gemini Notebook (NotebookLM) Client for Quaderno Companion.

Provides direct RPC and programmatic API integration with Google Gemini Notebook
(NotebookLM) via notebooklm-py to generate source-grounded, structured E-ink reading summaries.
"""

import asyncio
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)


class GeminiNotebookClient:
    """Client for querying Gemini Notebook (NotebookLM) via direct async RPCs."""

    def __init__(self, storage_path: Optional[Path] = None):
        self._custom_storage_path = storage_path

    def get_storage_path(self) -> Optional[Path]:
        """Resolve the path to the NotebookLM storage state auth file."""
        if self._custom_storage_path and self._custom_storage_path.exists():
            return self._custom_storage_path

        if settings.notebook_storage_path and settings.notebook_storage_path.exists():
            return settings.notebook_storage_path

        # Standard discovery locations
        candidates = [
            Path.home() / ".notebooklm" / "storage_state.json",
            Path.home() / ".notebooklm" / "profiles" / "default" / "storage_state.json",
            Path.home() / ".config" / "quaderno" / "notebooklm" / "data" / "browser_state" / "state.json",
            Path.home() / ".gemini" / "config" / "skills" / "notebooklm" / "data" / "browser_state" / "state.json",
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.stat().st_size > 30:
                return candidate

        return None

    def is_available(self) -> bool:
        """Check if notebooklm-py integration is available."""
        try:
            import notebooklm  # noqa: F401
            return True
        except ImportError:
            return False

    def is_authenticated(self) -> bool:
        """Check if active authentication state exists."""
        storage = self.get_storage_path()
        return storage is not None and storage.exists() and storage.stat().st_size > 30

    async def get_library_notebooks(self) -> Dict[str, Any]:
        """List registered notebooks in the user's NotebookLM account."""
        try:
            from notebooklm import NotebookLMClient
            storage = self.get_storage_path()
            storage_arg = str(storage) if storage else None

            async with NotebookLMClient.from_storage(path=storage_arg) as client:
                nbs = await client.notebooks.list()
                return {
                    "notebooks": [
                        {
                            "id": nb.id,
                            "title": getattr(nb, "title", "Untitled"),
                            "sources_count": getattr(nb, "sources_count", 0),
                        }
                        for nb in nbs
                    ]
                }
        except Exception as e:
            logger.warning(f"Failed to list NotebookLM notebooks: {e}")
            return {"notebooks": []}

    def extract_notebook_id(self, notebook_url_or_id: str) -> str:
        """Extract raw notebook ID from URL or return ID directly."""
        match = re.search(r"/notebook/([a-zA-Z0-9_-]+)", notebook_url_or_id)
        if match:
            return match.group(1)
        return notebook_url_or_id.strip()

    async def query(
        self,
        question: str,
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None,
        timeout_seconds: float = 120.0,
    ) -> str:
        """Ask a question to an existing Gemini Notebook via direct RPC."""
        from notebooklm import NotebookLMClient

        target = notebook_id or settings.notebook_id
        if not target and (notebook_url or settings.notebook_url):
            target = self.extract_notebook_id(notebook_url or settings.notebook_url or "")

        if not target:
            # Fallback: check first notebook in library
            lib = await self.get_library_notebooks()
            nbs = lib.get("notebooks", [])
            if nbs:
                target = nbs[0]["id"]

        if not target:
            raise ValueError("No target Notebook ID or URL provided for shared query.")

        clean_target = target.replace("\x00", "")
        clean_question = question.replace("\x00", "")

        storage = self.get_storage_path()
        storage_arg = str(storage) if storage else None

        logger.info(f"Querying shared Gemini Notebook {clean_target} via direct RPC...")

        async with NotebookLMClient.from_storage(path=storage_arg, timeout=timeout_seconds) as client:
            res = await client.chat.ask(clean_target, clean_question)
            raw_answer = res.answer if hasattr(res, "answer") else str(res)
            return self.clean_output(raw_answer)

    async def query_ephemeral(
        self,
        question: str,
        content_text: Optional[str] = None,
        source_file: Optional[str] = None,
        source_url: Optional[str] = None,
        title: Optional[str] = None,
        cleanup: bool = True,
        timeout_seconds: float = 180.0,
    ) -> str:
        """Spawn a dedicated ephemeral notebook, ingest source, query brief, and auto-cleanup."""
        from notebooklm import NotebookLMClient

        doc_title = (title or "Executive Brief").replace("\x00", "")[:80]
        clean_question = question.replace("\x00", "")

        storage = self.get_storage_path()
        storage_arg = str(storage) if storage else None

        logger.info(f"Spawning ephemeral Gemini Notebook for '{doc_title}' via direct RPC (cleanup={cleanup})...")

        async with NotebookLMClient.from_storage(path=storage_arg, timeout=timeout_seconds) as client:
            # 1. Create dedicated notebook
            nb = await client.notebooks.create(doc_title)
            nb_id = nb.id
            logger.info(f"✓ Ephemeral notebook created: {nb_id}")

            try:
                # 2. Ingest source
                if source_file and Path(source_file).exists():
                    clean_file = str(source_file).replace("\x00", "")
                    logger.info(f"Ingesting file source: {clean_file}...")
                    await client.sources.add_file(nb_id, clean_file, wait=True)

                elif source_url and str(source_url).startswith("http"):
                    clean_url = str(source_url).replace("\x00", "")
                    logger.info(f"Ingesting URL source: {clean_url}...")
                    await client.sources.add_url(nb_id, clean_url, wait=True)

                elif content_text:
                    clean_text = str(content_text).replace("\x00", "")
                    logger.info(f"Ingesting text source ({len(clean_text)} chars)...")
                    await client.sources.add_text(nb_id, doc_title, clean_text[:25000], wait=True)

                # 3. Query brief
                logger.info("Querying synthesis brief from Gemini Notebook...")
                res = await client.chat.ask(nb_id, clean_question)
                raw_answer = res.answer if hasattr(res, "answer") else str(res)
                return self.clean_output(raw_answer)

            finally:
                # 4. Clean up if requested
                if cleanup:
                    try:
                        await client.notebooks.delete(nb_id)
                        logger.info(f"✓ Cleaned up ephemeral notebook: {nb_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete ephemeral notebook {nb_id}: {e}")

    def clean_output(self, raw_stdout: str) -> str:
        """Clean command wrapper headers, follow-up footers, and ANSI escapes."""
        if not raw_stdout:
            return ""

        # 1. Strip ANSI escape sequences
        ansi_regex = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        text = ansi_regex.sub("", raw_stdout)

        # 2. Extract content between '====' separator blocks if present
        if "============================================================" in text:
            blocks = text.split("============================================================")
            if len(blocks) >= 3:
                text = blocks[2].strip()

        # 3. Strip follow-up reminder banner
        follow_up_phrase = "EXTREMELY IMPORTANT: Is that ALL you need to know?"
        if follow_up_phrase in text:
            text = text.split(follow_up_phrase)[0].strip()

        # 4. Remove banner prefixes
        clean_lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("💬 Asking:", "📚 Notebook:", "  🌐 Opening", "  ⏳ ", "  ✓ ", "  📤 ", "  ✅ ")):
                continue
            clean_lines.append(line)

        return "\n".join(clean_lines).strip()

    def parse_summary_response(
        self,
        raw_response: str,
        default_title: Optional[str] = None,
        target_pages: int = 1,
    ) -> Dict[str, Any]:
        """Parse Gemini Notebook response into structured E-ink summary dict."""
        cleaned = self.clean_output(raw_response)

        # 1. Try parsing JSON if model returned structured JSON
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict) and "key_takeaways" in data:
                    return {
                        "title": data.get("title") or default_title or "Executive Brief",
                        "key_takeaways": data.get("key_takeaways", []),
                        "sections": data.get("sections", {}),
                    }
            except Exception:
                pass

        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                data = json.loads(cleaned)
                if isinstance(data, dict) and "key_takeaways" in data:
                    return {
                        "title": data.get("title") or default_title or "Executive Brief",
                        "key_takeaways": data.get("key_takeaways", []),
                        "sections": data.get("sections", {}),
                    }
            except Exception:
                pass

        # 2. Parse Markdown structured response
        title = default_title or "Executive Brief"
        key_takeaways: List[str] = []
        sections: Dict[str, Any] = {}

        # Extract Title: # Title: ... or Title: ...
        title_match = re.search(r"^#+\s*(?:Title:\s*)?([^\n]+)", cleaned, re.MULTILINE | re.IGNORECASE)
        if title_match:
            extracted_title = title_match.group(1).strip()
            if not extracted_title.lower().startswith("key takeaway") and not extracted_title.lower().startswith("section"):
                title = extracted_title

        # Extract Key Takeaways section
        takeaways_match = re.search(
            r"##+\s*(?:Key Takeaways|Executive Summary|Highlights|Core Insights)\s*\n(.*?)(?=\n##|\Z)",
            cleaned,
            re.DOTALL | re.IGNORECASE,
        )
        if takeaways_match:
            takeaways_text = takeaways_match.group(1)
            bullets = [
                re.sub(r"^[-*•\d.]+\s*", "", line).strip()
                for line in takeaways_text.split("\n")
                if line.strip().startswith(("-", "*", "•", "1.", "2.", "3.", "4.", "5."))
            ]
            key_takeaways = [b for b in bullets if len(b) > 5]

        # Extract Sections: ### Heading \n Content
        section_matches = list(re.finditer(r"(?:^|\n)(?:###?)\s*([^\n#]+)\n(.*?)(?=\n###?|\Z)", cleaned, re.DOTALL))
        if section_matches:
            for sm in section_matches:
                sec_header = sm.group(1).strip()
                sec_body = sm.group(2).strip()
                if sec_header.lower() in ("key takeaways", "takeaways", "title"):
                    continue
                if sec_body:
                    sections[sec_header] = sec_body

        # Fallback if no sections or takeaways extracted
        if not key_takeaways:
            all_bullets = [
                re.sub(r"^[-*•\d.]+\s*", "", line).strip()
                for line in cleaned.split("\n")
                if line.strip().startswith(("-", "*", "•")) and len(line.strip()) > 10
            ]
            key_takeaways = all_bullets[: min(5, max(3, target_pages + 2))]

        if not key_takeaways:
            paras = [p.strip() for p in cleaned.split("\n\n") if len(p.strip()) > 30]
            key_takeaways = [p[:140] + "..." for p in paras[:3]]

        if not sections:
            paras = [p.strip() for p in cleaned.split("\n\n") if len(p.strip()) > 20]
            if len(paras) <= 2:
                sections["Executive Summary"] = cleaned
            else:
                chunk_size = max(1, len(paras) // target_pages)
                for i in range(target_pages):
                    start = i * chunk_size
                    end = (i + 1) * chunk_size if i < target_pages - 1 else len(paras)
                    chunk = paras[start:end]
                    if chunk:
                        sec_title = f"Section {i + 1}: Key Synthesis" if i > 0 else "Executive Overview"
                        sections[sec_title] = "\n\n".join(chunk)

        return {
            "title": title,
            "key_takeaways": key_takeaways or ["Synthesized via Gemini Notebook (NotebookLM)."],
            "sections": sections,
        }

    async def generate_summary(
        self,
        text_or_content: str,
        title: Optional[str] = None,
        pages: int = 1,
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None,
        mode: Optional[str] = None,
        cleanup: Optional[bool] = None,
        source_file: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        from quaderno_companion.agent.prompts import GEMINI_NOTEBOOK_SUMMARIZE_PROMPT, get_page_length_instruction
        clean_title = title.replace("\x00", "") if isinstance(title, str) else title
        prompt = GEMINI_NOTEBOOK_SUMMARIZE_PROMPT.format(
            length_instruction=get_page_length_instruction(pages),
        )

        target_mode = mode or settings.notebook_mode or "ephemeral"
        should_cleanup = cleanup if cleanup is not None else settings.notebook_cleanup

        # If explicit notebook_url or notebook_id is provided, use shared query
        if notebook_url or notebook_id or target_mode == "shared":
            try:
                raw_response = await self.query(
                    question=prompt,
                    notebook_url=notebook_url,
                    notebook_id=notebook_id,
                )
                if not raw_response:
                    return None
                return self.parse_summary_response(
                    raw_response=raw_response,
                    default_title=clean_title,
                    target_pages=pages,
                )
            except Exception as e:
                logger.warning(f"Shared Gemini Notebook summarization query failed: {e}")
                raise

        # Otherwise, use ephemeral dedicated notebook with auto-cleanup
        try:
            raw_response = await self.query_ephemeral(
                question=prompt,
                content_text=text_or_content,
                source_file=source_file,
                source_url=source_url,
                title=clean_title,
                cleanup=should_cleanup,
            )
            if not raw_response:
                return None
            return self.parse_summary_response(
                raw_response=raw_response,
                default_title=clean_title,
                target_pages=pages,
            )
        except Exception as e:
            logger.warning(f"Ephemeral Gemini Notebook summarization query failed: {e}")
            raise
