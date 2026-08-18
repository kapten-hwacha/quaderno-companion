"""Content Fetcher and Ingestion Engine.

Ingests articles, ArXiv papers, web pages, and local documents,
converting them into optimized E-ink PDF streams ready for Quaderno display.
"""

import hashlib
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path
import re
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
import pymupdf as fitz
from readability import Document

from quaderno_companion.config import settings
from quaderno_companion.pipeline.optimizer import EinkOptimizer
from quaderno_companion.pipeline.templates import EinkDocumentBuilder

logger = logging.getLogger(__name__)

ARXIV_PATTERN = re.compile(r"arxiv\.org/(?:abs|html|pdf)/([0-9]+\.[0-9]+(?:v[0-9]+)?)")


SENSITIVE_FILENAME_SUBSTRINGS = (
    "key.pem",
    "deviceid.dat",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    ".env",
)


@dataclass
class FetchedDocument:
    """Represents an ingested document ready for transmission."""
    title: str
    pdf_bytes: bytes
    source_url: Optional[str] = None
    filename: str = "document.pdf"
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash and self.pdf_bytes:
            self.content_hash = hashlib.sha256(self.pdf_bytes).hexdigest()[:16]


class ContentFetcher:
    """Fetcher for downloading and converting web / document content."""

    def __init__(self, profile_name: Optional[str] = None):
        self.profile = profile_name or settings.default_profile
        self.optimizer = EinkOptimizer(profile_name=self.profile)
        self.builder = EinkDocumentBuilder(profile_name=self.profile)

    def _validate_remote_url(self, url: str) -> None:
        """Validate URL to prevent SSRF against loopback, private networks, and cloud metadata."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme: '{parsed.scheme}'. Only http and https are allowed.")
        host = (parsed.hostname or "").lower().strip("[]")
        if not host:
            raise ValueError("Invalid URL: missing host.")

        # Block well-known cloud metadata hostnames
        if host in ("metadata.google.internal", "instance-data", "metadata") or host.endswith(".internal"):
            raise ValueError("Access to cloud metadata endpoints is blocked for security.")

        # Resolve host and inspect all target IP addresses (IPv4 & IPv6)
        try:
            addr_info = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        except socket.gaierror:
            # If DNS resolution fails, check if the string directly represents an IP
            addr_info = []

        resolved_ips = {item[4][0] for item in addr_info}
        if not resolved_ips:
            try:
                # Direct IP literal check
                resolved_ips.add(str(ipaddress.ip_address(host)))
            except ValueError:
                # Valid public domain that may be resolved later during request, or offline
                pass

        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if ip.is_loopback:
                raise ValueError(f"Access to local loopback endpoints is blocked for security ({ip_str}).")
            if ip.is_link_local or ip_str.startswith("169.254."):
                raise ValueError(f"Access to cloud metadata or link-local endpoints is blocked for security ({ip_str}).")
            if ip.is_private:
                raise ValueError(f"Access to private network endpoints is blocked for security ({ip_str}).")
            if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError(f"Access to reserved network endpoints is blocked for security ({ip_str}).")

    def _validate_local_path(self, path: Path) -> None:
        """Validate local file to prevent arbitrary extraction of sensitive credentials/system files."""
        resolved = path.resolve()
        path_str = str(resolved).lower()
        if any(bad in path_str for bad in SENSITIVE_FILENAME_SUBSTRINGS):
            raise ValueError("Access to credential or secret files is blocked for security.")
        if resolved.is_relative_to(Path("/etc")) or resolved.is_relative_to(Path("/var/root")):
            raise ValueError("Access to system directory files is blocked for security.")

    def _create_http_client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        """Create an HTTP client with SSRF redirect validation."""
        async def _check_redirect(response: httpx.Response) -> None:
            if response.is_redirect and "location" in response.headers:
                target_url = str(response.url.join(response.headers["location"]))
                self._validate_remote_url(target_url)

        return httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            event_hooks={"response": [_check_redirect]},
        )

    async def fetch(
        self,
        source_url_or_path: str,
        custom_title: Optional[str] = None,
        optimize_for_eink: bool = True,
    ) -> FetchedDocument:
        """Ingest content from URL or local file path."""
        # 1. Check if local path
        local_path = Path(source_url_or_path).expanduser().resolve()
        if local_path.is_file():
            self._validate_local_path(local_path)
            return await self._ingest_local_file(local_path, custom_title, optimize_for_eink)

        # 2. Check if URL
        parsed = urlparse(source_url_or_path.strip())
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            # Treat non-URL string as raw text snippet / quick note
            title = custom_title or "Quick Note"
            pdf_bytes = self.builder.render_article_pdf(
                title=title,
                content_html_or_text=source_url_or_path.strip(),
            )
            return FetchedDocument(
                title=title,
                pdf_bytes=pdf_bytes,
                filename=self._sanitize_filename(f"{title}.pdf"),
            )

        # 3. Validate URL against SSRF
        self._validate_remote_url(source_url_or_path)

        # 3. Check for ArXiv URL
        arxiv_match = ARXIV_PATTERN.search(source_url_or_path)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1)
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            logger.info(f"Resolved ArXiv paper {arxiv_id} to {pdf_url}")
            return await self._download_and_optimize_pdf(
                pdf_url,
                custom_title or f"ArXiv_{arxiv_id}",
                optimize_for_eink,
                original_url=source_url_or_path,
            )

        # 4. Fetch HTTP headers / content to detect content type
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 QuadernoCompanion/1.0 (contact@quaderno.internal)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with self._create_http_client(timeout=30.0) as client:
            response = await client.get(source_url_or_path, headers=headers)
            
            # Check for anti-bot WAF / Cloudflare challenge page before raise_for_status
            if self._is_waf_or_bot_challenge(response):
                domain = urlparse(source_url_or_path).netloc
                raise ValueError(
                    f"The server at '{domain}' is protected by an anti-bot challenge (WAF / Cloudflare). "
                    f"Please open the document in your browser and use 'Push Local File' or Preview sync."
                )

            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()

            # Direct PDF
            if "application/pdf" in content_type or response.content.startswith(b"%PDF"):
                raw_pdf = response.content
                title = custom_title or self._extract_filename_from_url(source_url_or_path)
                
                if optimize_for_eink:
                    pdf_bytes = self.optimizer.optimize_pdf(raw_pdf)
                else:
                    pdf_bytes = raw_pdf

                return FetchedDocument(
                    title=title,
                    pdf_bytes=pdf_bytes,
                    source_url=source_url_or_path,
                    filename=self._sanitize_filename(f"{title}.pdf"),
                )

            # HTML / Web page Article (e.g. Wikipedia, blog, news, documentation)
            html_text = response.text
            return self._extract_article_and_render_pdf(
                html_text,
                source_url=source_url_or_path,
                custom_title=custom_title,
            )

    async def _ingest_local_file(
        self,
        path: Path,
        custom_title: Optional[str] = None,
        optimize_for_eink: bool = True,
    ) -> FetchedDocument:
        """Ingest a local PDF, Markdown, or text file."""
        suffix = path.suffix.lower()
        title = custom_title or path.stem

        if suffix == ".pdf":
            raw_bytes = path.read_bytes()
            if optimize_for_eink:
                pdf_bytes = self.optimizer.optimize_pdf(raw_bytes)
            else:
                pdf_bytes = raw_bytes

            return FetchedDocument(
                title=title,
                pdf_bytes=pdf_bytes,
                filename=self._sanitize_filename(f"{title}.pdf"),
            )

        elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
            img_doc = fitz.open(str(path))
            pdf_bytes_tmp = img_doc.convert_to_pdf()
            img_doc.close()
            if optimize_for_eink:
                pdf_bytes = self.optimizer.optimize_pdf(pdf_bytes_tmp)
            else:
                pdf_bytes = pdf_bytes_tmp

            return FetchedDocument(
                title=title,
                pdf_bytes=pdf_bytes,
                filename=self._sanitize_filename(f"{title}.pdf"),
            )

        elif suffix in (".md", ".txt"):
            content = path.read_text(encoding="utf-8")
            pdf_bytes = self.builder.render_article_pdf(
                title=title,
                content_html_or_text=content,
            )
            return FetchedDocument(
                title=title,
                pdf_bytes=pdf_bytes,
                filename=self._sanitize_filename(f"{title}.pdf"),
            )

        elif suffix in (".html", ".htm"):
            content = path.read_text(encoding="utf-8")
            return self._extract_article_and_render_pdf(content, custom_title=title)

        else:
            raise ValueError(f"Unsupported file format: {suffix}")

    def _is_waf_or_bot_challenge(self, response: httpx.Response) -> bool:
        """Detect anti-bot / WAF challenge interstitials (Cloudflare, AWS WAF, Imperva, Datadome)."""
        headers = response.headers

        # 1. Anti-bot / Challenge HTTP headers
        if any(h in headers for h in ("cf-mitigated", "cf-chl-bypass", "x-amzn-waf-action", "x-datadome")):
            return True

        # 2. Blocked status codes accompanied by WAF server headers
        if response.status_code in (403, 429, 503):
            server = headers.get("server", "").lower()
            if any(s in server for s in ("cloudflare", "ddos-guard", "imperva", "incapsula", "akamai")):
                return True

        # 3. Known challenge payload byte tokens in HTML
        content = response.content
        challenge_tokens = (
            b"awsWaf",
            b"cf-challenge",
            b"Just a moment...",
            b"gokuProps",
            b"challenge-running",
            b"_cf_chl_opt",
            b"Turnstile",
            b"cf-browser-verification",
        )
        return any(token in content for token in challenge_tokens)

    async def _download_and_optimize_pdf(
        self,
        pdf_url: str,
        title: str,
        optimize_for_eink: bool,
        original_url: Optional[str] = None,
    ) -> FetchedDocument:
        """Download remote PDF and pass through optimization."""
        self._validate_remote_url(pdf_url)
        async with self._create_http_client(timeout=60.0) as client:
            resp = await client.get(pdf_url)
            if self._is_waf_or_bot_challenge(resp):
                domain = urlparse(pdf_url).netloc
                raise ValueError(
                    f"The server at '{domain}' is protected by an anti-bot challenge (WAF / Cloudflare). "
                    f"Please open the PDF in your browser and use 'Push Local File' or Preview sync."
                )
            resp.raise_for_status()
            raw_pdf = resp.content

        if optimize_for_eink:
            pdf_bytes = self.optimizer.optimize_pdf(raw_pdf)
        else:
            pdf_bytes = raw_pdf

        return FetchedDocument(
            title=title,
            pdf_bytes=pdf_bytes,
            source_url=original_url or pdf_url,
            filename=self._sanitize_filename(f"{title}.pdf"),
        )

    def _extract_article_and_render_pdf(
        self,
        html_text: str,
        source_url: Optional[str] = None,
        custom_title: Optional[str] = None,
    ) -> FetchedDocument:
        """Extract readable article content from HTML (including tables) and format as E-ink PDF."""
        doc = Document(html_text)
        title = custom_title or doc.title()
        cleaned_html = doc.summary(html_partial=True)

        soup = BeautifulSoup(cleaned_html, "html.parser")
        for bad in soup(["script", "style", "nav", "footer", "form", "noscript"]):
            bad.extract()

        elements = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote"])

        if not elements:
            body_soup = BeautifulSoup(html_text, "html.parser")
            for bad in body_soup(["script", "style", "nav", "footer", "form", "noscript"]):
                bad.extract()
            body_content = body_soup.find("div", id="bodyContent") or body_soup.find("div", class_="mw-parser-output") or body_soup.body or body_soup
            elements = body_content.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote"])

        pdf_bytes = self.builder.render_article_pdf(
            title=title,
            content_html_or_text="",
            source_url=source_url,
            soup_elements=elements,
        )

        return FetchedDocument(
            title=title,
            pdf_bytes=pdf_bytes,
            source_url=source_url,
            filename=self._sanitize_filename(f"{title}.pdf"),
        )

    def _extract_filename_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        name = path.split("/")[-1] if "/" in path else "document"
        if name.endswith(".pdf"):
            name = name[:-4]
        return name or "document"

    def _sanitize_filename(self, name: str) -> str:
        # Keep alphanumeric, dashes, dots, underscores
        clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)
        return clean[:120]
