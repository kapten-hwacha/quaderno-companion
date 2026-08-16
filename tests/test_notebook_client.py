"""Unit tests for Gemini Notebook (NotebookLM) client using notebooklm-py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quaderno_companion.config import settings
from quaderno_companion.pipeline.notebook_client import GeminiNotebookClient


def test_clean_output():
    """Verify clean_output strips ANSI codes, banners, and follow-up footer."""
    client = GeminiNotebookClient()

    raw_text = (
        "\x1b[32m💬 Asking: What is quantum computing?\x1b[0m\n"
        "📚 Notebook: https://notebooklm.google.com/notebook/123\n"
        "  🌐 Opening notebook...\n"
        "  ⏳ Waiting for query input...\n"
        "  ✓ Found input\n"
        "  ⏳ Typing question...\n"
        "  📤 Submitting...\n"
        "  ⏳ Waiting for answer...\n"
        "  ✅ Got answer!\n"
        "============================================================\n"
        "Question: What is quantum computing?\n"
        "============================================================\n"
        "# Quantum Computing Overview\n\n"
        "## Key Takeaways\n"
        "- Qubits leverage superposition\n"
        "- Entanglement enables parallelism\n\n"
        "### Technical Details\n"
        "Quantum computing utilizes quantum mechanical phenomena.\n"
        "============================================================\n\n"
        "EXTREMELY IMPORTANT: Is that ALL you need to know? You can always ask another question!"
    )

    cleaned = client.clean_output(raw_text)
    assert "💬 Asking:" not in cleaned
    assert "EXTREMELY IMPORTANT" not in cleaned
    assert "Quantum Computing Overview" in cleaned
    assert "Qubits leverage superposition" in cleaned
    assert "Technical Details" in cleaned


def test_parse_markdown_response():
    """Verify parse_summary_response correctly extracts title, takeaways, and sections from markdown."""
    client = GeminiNotebookClient()

    markdown_text = (
        "# Quantum Architecture 2026\n\n"
        "## Key Takeaways\n"
        "- Breakthrough in error correction\n"
        "- 10,000 physical qubits operational\n"
        "- Near-zero thermal noise\n\n"
        "### Hardware Milestones\n"
        "The superconducting transmon design achieved 99.99% fidelity.\n\n"
        "### Practical Implications\n"
        "Commercial simulation of room-temperature catalysts is now feasible.\n"
    )

    res = client.parse_summary_response(markdown_text, default_title="Fallback Title", target_pages=2)
    assert res["title"] == "Quantum Architecture 2026"
    assert len(res["key_takeaways"]) == 3
    assert "Breakthrough in error correction" in res["key_takeaways"][0]
    assert "Hardware Milestones" in res["sections"]
    assert "Practical Implications" in res["sections"]
    assert "superconducting transmon" in res["sections"]["Hardware Milestones"]


def test_parse_json_response():
    """Verify parse_summary_response handles JSON formatted responses."""
    client = GeminiNotebookClient()

    json_block = """
```json
{
  "title": "Autonomous Agent Architecture",
  "key_takeaways": [
    "Zero-latency deterministic fast path",
    "Source-grounded synthesis with Gemini Notebook",
    "Native E-ink contrast optimization"
  ],
  "sections": {
    "Architecture Overview": "The agent orchestrator routes user intents seamlessly.",
    "E-Ink Ergonomics": "Custom typography and layout ensure instant legibility."
  }
}
```
"""
    res = client.parse_summary_response(json_block, default_title="Default")
    assert res["title"] == "Autonomous Agent Architecture"
    assert len(res["key_takeaways"]) == 3
    assert "Architecture Overview" in res["sections"]
    assert "E-Ink Ergonomics" in res["sections"]


def test_parse_unstructured_fallback():
    """Verify parse_summary_response creates reasonable fallback structure when given unstructured text."""
    client = GeminiNotebookClient()

    unstructured = (
        "Here is the summary you asked for.\n\n"
        "- Primary discovery in neuroscience\n"
        "- Synaptic plasticity is modulated by sleep cycles\n"
        "- Memory consolidation occurs during deep slow-wave phase\n\n"
        "Researchers recorded neural activity across 500 subjects over two years. "
        "The results demonstrated marked improvements in retention when sleep was uninterrupted."
    )

    res = client.parse_summary_response(unstructured, default_title="Brain Research", target_pages=1)
    assert res["title"] == "Brain Research"
    assert len(res["key_takeaways"]) >= 3
    assert len(res["sections"]) >= 1


def test_extract_notebook_id():
    """Verify extracting notebook ID from full URLs or raw IDs."""
    client = GeminiNotebookClient()
    assert client.extract_notebook_id("https://notebook.google.com/notebook/test-123") == "test-123"
    assert client.extract_notebook_id("https://notebooklm.google.com/u/0/notebook/abc-987") == "abc-987"
    assert client.extract_notebook_id("direct-id-xyz") == "direct-id-xyz"


def test_is_available_and_auth_check(tmp_path: Path):
    """Verify client availability and auth state discovery."""
    client = GeminiNotebookClient()
    assert client.is_available() is True

    fake_storage = tmp_path / "storage_state.json"
    fake_storage.write_text('{"cookies": [{"name": "SID", "value": "xyz"}]}')

    custom_client = GeminiNotebookClient(storage_path=fake_storage)
    assert custom_client.is_authenticated() is True
    assert custom_client.get_storage_path() == fake_storage


@pytest.mark.asyncio
async def test_query_with_notebooklm_client():
    """Verify query() calls NotebookLMClient.chat.ask via direct RPC."""
    client = GeminiNotebookClient()

    mock_res = MagicMock()
    mock_res.answer = "# Direct RPC Answer\n## Key Takeaways\n- RPC Point 1"

    mock_client_instance = AsyncMock()
    mock_client_instance.chat.ask.return_value = mock_res

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client_instance
    mock_ctx.__aexit__.return_value = None

    with patch("notebooklm.NotebookLMClient.from_storage", return_value=mock_ctx):
        ans = await client.query(
            question="What is this?",
            notebook_id="test-nb-456",
        )
        assert "# Direct RPC Answer" in ans
        mock_client_instance.chat.ask.assert_called_once_with("test-nb-456", "What is this?")


@pytest.mark.asyncio
async def test_query_ephemeral_with_notebooklm_client(tmp_path: Path):
    """Verify query_ephemeral creates notebook, ingests source, asks, and deletes."""
    client = GeminiNotebookClient()

    mock_nb = MagicMock()
    mock_nb.id = "ephemeral-nb-789"

    mock_ask_res = MagicMock()
    mock_ask_res.answer = "# Ephemeral RPC Result\n## Key Takeaways\n- Point A\n- Point B"

    mock_client_instance = AsyncMock()
    mock_client_instance.notebooks.create.return_value = mock_nb
    mock_client_instance.sources.add_text = AsyncMock()
    mock_client_instance.sources.add_file = AsyncMock()
    mock_client_instance.sources.add_url = AsyncMock()
    mock_client_instance.chat.ask.return_value = mock_ask_res
    mock_client_instance.notebooks.delete = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client_instance
    mock_ctx.__aexit__.return_value = None

    sample_file = tmp_path / "sample.pdf"
    sample_file.write_text("%PDF-1.4 dummy")

    with patch("notebooklm.NotebookLMClient.from_storage", return_value=mock_ctx):
        # 1. Test text ingestion
        res_text = await client.query_ephemeral(
            question="Summarize this text",
            content_text="Detailed article content",
            title="Text Doc",
            cleanup=True,
        )
        assert "# Ephemeral RPC Result" in res_text
        mock_client_instance.notebooks.create.assert_called_with("Text Doc")
        mock_client_instance.sources.add_text.assert_called_once_with("ephemeral-nb-789", "Text Doc", "Detailed article content", wait=True)
        mock_client_instance.notebooks.delete.assert_called_with("ephemeral-nb-789")

        # 2. Test file ingestion
        mock_client_instance.notebooks.delete.reset_mock()
        res_file = await client.query_ephemeral(
            question="Summarize file",
            source_file=str(sample_file),
            title="File Doc",
            cleanup=True,
        )
        assert "# Ephemeral RPC Result" in res_file
        mock_client_instance.sources.add_file.assert_called_once_with("ephemeral-nb-789", str(sample_file), wait=True)
        mock_client_instance.notebooks.delete.assert_called_once_with("ephemeral-nb-789")


@pytest.mark.asyncio
async def test_get_library_notebooks():
    """Verify get_library_notebooks lists notebooks using NotebookLMClient."""
    client = GeminiNotebookClient()

    nb1 = MagicMock(id="nb-1", title="Quantum Research", sources_count=3)
    nb2 = MagicMock(id="nb-2", title="Machine Learning", sources_count=5)

    mock_client_instance = AsyncMock()
    mock_client_instance.notebooks.list.return_value = [nb1, nb2]

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client_instance
    mock_ctx.__aexit__.return_value = None

    with patch("notebooklm.NotebookLMClient.from_storage", return_value=mock_ctx):
        lib = await client.get_library_notebooks()
        assert len(lib["notebooks"]) == 2
        assert lib["notebooks"][0]["id"] == "nb-1"
        assert lib["notebooks"][0]["title"] == "Quantum Research"


@pytest.mark.asyncio
async def test_generate_summary_ephemeral_mode():
    """Verify generate_summary() dispatches to query_ephemeral when mode is ephemeral."""
    client = GeminiNotebookClient()

    mock_response = (
        "# Ephemeral Brief\n\n"
        "## Key Takeaways\n"
        "- Clean context isolation\n"
        "- Automatic lifecycle cleanup\n\n"
        "### Core Findings\n"
        "Ephemeral creation ensures no crosstalk.\n"
    )

    with patch.object(client, "query_ephemeral", new_callable=AsyncMock) as mock_eph:
        mock_eph.return_value = mock_response

        summary = await client.generate_summary(
            text_or_content="Article text here...",
            title="Ephemeral Test",
            pages=1,
            mode="ephemeral",
            cleanup=True,
        )

        assert summary is not None
        assert summary["title"] == "Ephemeral Brief"
        mock_eph.assert_called_once()
        call_kwargs = mock_eph.call_args.kwargs
        assert call_kwargs["cleanup"] is True
        assert call_kwargs["title"] == "Ephemeral Test"
