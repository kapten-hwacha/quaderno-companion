"""Clipboard image utilities for Quaderno Companion.

Enables capturing the currently open page or live screen from Quaderno
and placing it onto the host computer's system clipboard as an image.
"""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from quaderno_companion.config import settings
from quaderno_companion.device.manager import device_manager

logger = logging.getLogger(__name__)


def set_clipboard_image(image_bytes: bytes, image_format: str = "png") -> bool:
    """Put raw image bytes directly onto the host operating system clipboard.

    Supports macOS native NSPasteboard (AppKit), with fallbacks for
    macOS osascript and Linux (wl-copy / xclip).
    """
    if not image_bytes:
        raise ValueError("Image bytes cannot be empty.")

    # 1. macOS Native Implementation via AppKit / NSPasteboard
    if sys.platform == "darwin":
        try:
            import AppKit  # type: ignore[import-untyped]

            pasteboard = AppKit.NSPasteboard.generalPasteboard()
            pasteboard.clearContents()

            nsdata = AppKit.NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
            nsimage = AppKit.NSImage.alloc().initWithData_(nsdata)
            if nsimage is not None:
                success = pasteboard.writeObjects_([nsimage])
                if success:
                    logger.debug("Successfully copied image to macOS clipboard via AppKit.")
                    return True
        except Exception as e:
            logger.debug(f"AppKit NSPasteboard write failed, falling back to osascript: {e}")

        # Fallback for macOS via osascript
        tmp_file = None
        try:
            suffix = f".{image_format.lower()}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_file = tmp.name

            osa_type = "«class PNGf»" if image_format.lower() == "png" else "JPEG picture"
            script = f'set the clipboard to (read (POSIX file "{tmp_file}") as {osa_type})'
            subprocess.run(["osascript", "-e", script], check=True, timeout=5.0)
            logger.debug("Successfully copied image to macOS clipboard via osascript.")
            return True
        except Exception as osa_err:
            logger.warning(f"macOS osascript clipboard write failed: {osa_err}")
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except Exception:
                    pass

        return False

    # 2. Linux Wayland (wl-copy)
    mime = f"image/{image_format.lower()}"
    try:
        proc = subprocess.run(
            ["wl-copy", "-t", mime],
            input=image_bytes,
            check=True,
            timeout=5.0,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass

    # 3. Linux X11 (xclip)
    try:
        proc = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", mime, "-i"],
            input=image_bytes,
            check=True,
            timeout=5.0,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass

    logger.warning("No compatible clipboard image handler found for current platform.")
    return False


async def get_active_page_image(
    page: Optional[int] = None,
    dpi: int = 200,
    from_screen: bool = False,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """Retrieve an image of the currently open page or live screen.

    Args:
        page: Optional specific page number (1-based). If None, uses active page.
        dpi: Target resolution for PDF rendering (default: 200).
        from_screen: If True, captures physical Quaderno screen (screenshot)
                     instead of rendering document page vector data.

    Returns:
        Tuple of (image_bytes, format_string, metadata_dict)
    """
    import pymupdf

    # Load latest reading state
    state = getattr(device_manager, "_reading_state", None)
    if not state or not state.document_id:
        device_manager._load_persisted_state()
        state = device_manager._reading_state

    # Best-effort refresh from hardware if connected
    try:
        status = await device_manager.get_status()
        if status.reading_state and status.reading_state.document_id:
            state = status.reading_state
    except Exception as e:
        logger.debug(f"Could not refresh live status for page capture: {e}")

    # Case A: Live physical screen capture requested
    if from_screen:
        client = await device_manager.get_client()
        if not client:
            raise RuntimeError("Cannot capture screen: Quaderno device is offline.")
        img_bytes = await client.take_screenshot()
        meta = {
            "document_id": state.document_id if state else None,
            "title": (state.title if state and state.title else "Quaderno Screen"),
            "page": state.current_page if state else 1,
            "total_pages": state.total_pages if state else 1,
            "from_screen": True,
            "format": "jpeg",
        }
        return img_bytes, "jpeg", meta

    # Case B: Render specific page of active document
    if not state or not state.document_id:
        # If no active document is tracked, try falling back to live screen capture
        try:
            client = await device_manager.get_client()
            if client:
                logger.info("No active document tracked; falling back to device screenshot.")
                img_bytes = await client.take_screenshot()
                return img_bytes, "jpeg", {
                    "document_id": None,
                    "title": "Quaderno Screen",
                    "page": 1,
                    "total_pages": 1,
                    "from_screen": True,
                    "format": "jpeg",
                }
        except Exception:
            pass
        raise ValueError("No active document currently open on Quaderno.")

    target_page = page if page is not None else (state.current_page or 1)
    title = state.title or "active_document"
    doc_id = state.document_id

    # 1. Locate local PDF file in sync folder
    candidate: Optional[Path] = None
    if settings.sync_dir.exists():
        direct = settings.sync_dir / f"{title}.pdf"
        if direct.exists():
            candidate = direct
        else:
            # Search subfolders for matching title
            for match in settings.sync_dir.rglob(f"{title}.pdf"):
                candidate = match
                break
            if not candidate and state.remote_path:
                rel = state.remote_path.replace("Document/", "").lstrip("/")
                sub_match = settings.sync_dir / rel
                if sub_match.exists():
                    candidate = sub_match

    pdf_bytes: Optional[bytes] = None
    if candidate and candidate.exists():
        try:
            pdf_bytes = candidate.read_bytes()
        except Exception as e:
            logger.warning(f"Could not read local PDF {candidate}: {e}")

    # 2. Download from device if not available locally
    if not pdf_bytes:
        try:
            client = await device_manager.get_client()
            if client:
                logger.info(f"Downloading active document '{title}' ({doc_id}) from Quaderno...")
                pdf_bytes = await client.download_document_async(doc_id)
        except Exception as dl_err:
            logger.debug(f"Failed to download document from device: {dl_err}")

    # 3. If PDF still unavailable, fallback to live screen capture
    if not pdf_bytes:
        logger.warning(f"Document PDF '{title}' not found locally or remotely. Falling back to screenshot.")
        client = await device_manager.get_client()
        if client:
            img_bytes = await client.take_screenshot()
            return img_bytes, "jpeg", {
                "document_id": doc_id,
                "title": title,
                "page": target_page,
                "total_pages": state.total_pages or 1,
                "from_screen": True,
                "format": "jpeg",
            }
        raise FileNotFoundError(f"Document '{title}' not found locally in {settings.sync_dir} and device is unreachable.")

    # 4. Render page with PyMuPDF
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError(f"Document '{title}' contains no pages.")

    clamped_page = max(1, min(total_pages, target_page))
    page_obj = doc[clamped_page - 1]
    pix = page_obj.get_pixmap(dpi=dpi)
    png_bytes = pix.tobytes("png")
    doc.close()

    meta = {
        "document_id": doc_id,
        "title": title,
        "page": clamped_page,
        "total_pages": total_pages,
        "from_screen": False,
        "format": "png",
    }
    return png_bytes, "png", meta


async def copy_active_page_to_clipboard(
    page: Optional[int] = None,
    dpi: int = 200,
    from_screen: bool = False,
) -> Dict[str, Any]:
    """Retrieve active Quaderno page image and write it to the host clipboard.

    Returns:
        Dict with status, title, page, total_pages, format, and byte size.
    """
    img_bytes, img_format, meta = await get_active_page_image(
        page=page,
        dpi=dpi,
        from_screen=from_screen,
    )

    success = set_clipboard_image(image_bytes=img_bytes, image_format=img_format)
    if not success:
        raise RuntimeError("Failed to set image onto system clipboard.")

    return {
        "status": "success",
        "message": f"Copied page {meta.get('page', 1)} of '{meta.get('title', 'Document')}' to clipboard.",
        "title": meta.get("title"),
        "page": meta.get("page"),
        "total_pages": meta.get("total_pages"),
        "from_screen": meta.get("from_screen", False),
        "format": img_format,
        "size_bytes": len(img_bytes),
    }
