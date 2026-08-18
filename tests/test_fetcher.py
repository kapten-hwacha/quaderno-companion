"""Tests for Content Fetcher and Ingestion."""

from pathlib import Path
import pytest
from quaderno_companion.pipeline.fetcher import ContentFetcher, FetchedDocument


@pytest.mark.asyncio
async def test_fetch_local_markdown_file(tmp_path: Path):
    """Verify local markdown file is parsed and compiled to PDF."""
    md_file = tmp_path / "research_notes.md"
    md_file.write_text(
        "# Fast Multi-Agent Architectures\n\n"
        "Autonomous agents leverage localized memory caches and tool routing.\n\n"
        "## Core Principles\n\n"
        "- Zero-latency tool calls\n"
        "- E-ink optimized reading displays\n"
        "- Resilient Bluetooth PAN failover\n"
    )

    fetcher = ContentFetcher(profile_name="A4")
    doc = await fetcher.fetch(str(md_file))

    assert isinstance(doc, FetchedDocument)
    assert doc.title == "research_notes"
    assert len(doc.pdf_bytes) > 0
    assert doc.filename == "research_notes.pdf"


@pytest.mark.asyncio
async def test_extract_article_from_html():
    """Verify HTML parsing produces clean structured PDF."""
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Understanding E-Paper Latency</title></head>
    <body>
        <article>
            <h1>Understanding E-Paper Latency</h1>
            <p>E-paper displays have unique physical characteristics dictated by electrophoretic microcapsules.</p>
            <h2>Waveform Driving</h2>
            <p>Modern Carta 1200 panels support Regal waveforms to eliminate ghosting without full-screen flash refreshes.</p>
            <ul>
                <li>Fast partial refresh: 100-200ms</li>
                <li>Full clean cycle: 450ms</li>
            </ul>
        </article>
    </body>
    </html>
    """
    fetcher = ContentFetcher(profile_name="A4")
    doc = fetcher._extract_article_and_render_pdf(
        sample_html,
        source_url="https://example.com/epaper-guide",
    )

    assert doc.title == "Understanding E-Paper Latency"
    assert len(doc.pdf_bytes) > 0
    assert doc.source_url == "https://example.com/epaper-guide"


@pytest.mark.asyncio
async def test_full_pipeline_html_to_eink_pdf_integration(tmp_path: Path):
    """Integration test: Ingest local HTML file through full fetch -> optimize pipeline and verify with fitz."""
    import pymupdf as fitz
    from quaderno_companion.config import SCREEN_PROFILES

    html_file = tmp_path / "article.html"
    html_file.write_text("""
    <html>
      <head><title>Quaderno Pipeline Test</title></head>
      <body>
        <main>
          <h1>Quaderno Integration Architecture</h1>
          <p>Testing end-to-end PDF generation, margin trimming, and E-ink compression.</p>
          <table border="1">
            <tr><th>Metric</th><th>Target</th></tr>
            <tr><td>Target DPI</td><td>207</td></tr>
            <tr><td>Latency</td><td>&lt;100ms</td></tr>
          </table>
        </main>
      </body>
    </html>
    """)

    fetcher = ContentFetcher(profile_name="A4")
    doc = await fetcher.fetch(str(html_file), optimize_for_eink=True)

    assert isinstance(doc, FetchedDocument)
    assert len(doc.pdf_bytes) > 0
    assert doc.filename.endswith(".pdf")

    # Open with PyMuPDF and verify structure
    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
    assert len(pdf_doc) >= 1
    page = pdf_doc[0]
    rect = page.rect
    prof = SCREEN_PROFILES["A4"]
    target_w = (prof.width / prof.dpi) * 72.0
    target_h = (prof.height / prof.dpi) * 72.0
    assert abs(rect.width - target_w) < 2.0
    assert abs(rect.height - target_h) < 2.0
    pdf_doc.close()


@pytest.mark.asyncio
async def test_waf_challenge_detection():
    """Verify that WAF and Cloudflare challenge interstitials raise informative ValueError."""
    import httpx
    fetcher = ContentFetcher(profile_name="A4")

    # Cloudflare 403 response with Server: cloudflare
    cf_resp = httpx.Response(
        status_code=403,
        headers={"Server": "cloudflare", "cf-ray": "12345"},
        content=b"error code: 1020",
    )
    assert fetcher._is_waf_or_bot_challenge(cf_resp) is True

    # Cloudflare 200 interstitial page
    cf_interstitial = httpx.Response(
        status_code=200,
        content=b"<!DOCTYPE html><html><title>Just a moment...</title><body>Checking your browser</body></html>",
    )
    assert fetcher._is_waf_or_bot_challenge(cf_interstitial) is True

    # Normal HTML 200 response
    normal_resp = httpx.Response(
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><body><h1>Hello World</h1></body></html>",
    )
    assert fetcher._is_waf_or_bot_challenge(normal_resp) is False


@pytest.mark.asyncio
async def test_ssrf_url_validation():
    """Verify that cloud metadata, loopback, private networks, and invalid schemes are blocked."""
    fetcher = ContentFetcher(profile_name="A4")

    # Cloud metadata SSRF
    with pytest.raises(ValueError, match="cloud metadata"):
        fetcher._validate_remote_url("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(ValueError, match="cloud metadata"):
        fetcher._validate_remote_url("http://metadata.google.internal/computeMetadata/v1/")

    # Loopback SSRF
    with pytest.raises(ValueError, match="loopback"):
        fetcher._validate_remote_url("http://127.0.0.1:8000/secret")

    with pytest.raises(ValueError, match="loopback"):
        fetcher._validate_remote_url("http://localhost:5000/api/device/status")

    with pytest.raises(ValueError, match="loopback"):
        fetcher._validate_remote_url("http://[::1]:8000/secret")

    # RFC1918 Private Network SSRF
    with pytest.raises(ValueError, match="private network"):
        fetcher._validate_remote_url("http://192.168.1.1/admin")

    with pytest.raises(ValueError, match="private network"):
        fetcher._validate_remote_url("http://10.0.0.5:8080/metrics")

    with pytest.raises(ValueError, match="private network"):
        fetcher._validate_remote_url("http://172.16.0.1/status")

    # Invalid scheme
    with pytest.raises(ValueError, match="scheme"):
        fetcher._validate_remote_url("file:///etc/passwd")


@pytest.mark.asyncio
async def test_ssrf_redirect_validation():
    """Verify that redirect hooks intercept and block redirects to private or cloud metadata IPs."""
    import httpx
    fetcher = ContentFetcher(profile_name="A4")

    client = fetcher._create_http_client()
    # Mock redirect response with Location header targeting 169.254.169.254
    req = httpx.Request("GET", "https://public-site.com/redirect")
    redirect_resp = httpx.Response(
        status_code=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        request=req,
    )

    hook = client.event_hooks["response"][0]
    with pytest.raises(ValueError, match="cloud metadata"):
        await hook(redirect_resp)

    # Mock redirect response with Location header targeting private IP
    redirect_private = httpx.Response(
        status_code=301,
        headers={"Location": "http://192.168.1.1/router-login"},
        request=req,
    )
    with pytest.raises(ValueError, match="private network"):
        await hook(redirect_private)

    await client.aclose()


@pytest.mark.asyncio
async def test_sensitive_local_path_validation(tmp_path: Path):
    """Verify that attempts to ingest private keys, credentials, or sensitive files are blocked."""
    fetcher = ContentFetcher(profile_name="A4")

    # Sensitive key file
    key_file = tmp_path / "key.pem"
    key_file.write_text("PRIVATE KEY")
    with pytest.raises(ValueError, match="blocked for security"):
        fetcher._validate_local_path(key_file)

    # Sensitive env file
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=123")
    with pytest.raises(ValueError, match="blocked for security"):
        fetcher._validate_local_path(env_file)



