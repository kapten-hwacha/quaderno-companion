"""Active macOS window capture and E-ink optimization trigger."""

import io
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional, Tuple

try:
    import AppKit as _AppKit  # type: ignore[import-untyped]
    import Quartz as _Quartz  # type: ignore[import-untyped]
    AppKit: Any = _AppKit
    Quartz: Any = _Quartz
except Exception:
    AppKit = None
    Quartz = None

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)


def crop_to_aspect_ratio(img: Any, target_w: int, target_h: int) -> Any:
    """Center-crop image to match target aspect ratio (target_w / target_h) to fill the screen edge-to-edge."""
    target_ratio = target_w / target_h
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # Wider than target: crop left and right sides
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        return img.crop((left, 0, left + new_width, img.height))
    else:
        # Taller than target: crop top and bottom
        new_height = int(img.width / target_ratio)
        top = (img.height - new_height) // 2
        return img.crop((0, top, img.width, top + new_height))


def _capture_screen_to_file(target_png_path: str, front_win_id: Optional[Any] = None) -> bool:
    """Capture screen or window across macOS (screencapture) and Linux (grim, maim, scrot, import)."""
    # 1. macOS
    if sys.platform == "darwin":
        try:
            if front_win_id:
                res = subprocess.run(
                    ["screencapture", "-l", str(front_win_id), "-o", "-x", target_png_path],
                    capture_output=True,
                    check=False,
                )
                if res.returncode == 0:
                    return True
            res = subprocess.run(
                ["screencapture", "-m", "-x", target_png_path],
                capture_output=True,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    # 2. Linux Wayland (grim)
    try:
        res = subprocess.run(["grim", target_png_path], capture_output=True, check=False)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 3. Linux X11 (maim active window or root)
    try:
        if front_win_id:
            res = subprocess.run(["maim", "-i", str(front_win_id), target_png_path], capture_output=True, check=False)
            if res.returncode == 0:
                return True
        res = subprocess.run(["maim", target_png_path], capture_output=True, check=False)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 4. Linux X11 (scrot)
    try:
        res = subprocess.run(["scrot", target_png_path], capture_output=True, check=False)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 5. Linux ImageMagick (import)
    try:
        res = subprocess.run(["import", "-window", "root", target_png_path], capture_output=True, check=False)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    return False


def capture_active_window_pdf(
    profile_name: Optional[str] = None,
    auto_rotate: bool = True,
    crop_to_fill: bool = True,
) -> Tuple[Path, str, str]:
    """Capture the frontmost active window and convert it to an E-ink optimized PDF.

    Args:
        profile_name: Target device profile ('A4' or 'A5').
        auto_rotate: Auto-rotate landscape window captures 90° clockwise for full-screen portrait reading.
        crop_to_fill: Center-crop to the Quaderno's exact 3:4 ratio to maximize zoom and fill screen edge-to-edge.

    Returns:
        Tuple of (pdf_file_path, filename, title).
    """
    target_profile = profile_name or settings.default_profile
    app_name = "Window"
    win_title = ""
    front_win_id = None

    # macOS AppKit / Quartz resolution
    try:
        if AppKit:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                app_name = str(app.localizedName() or "Window")
                pid = app.processIdentifier()

                if Quartz:
                    windows = Quartz.CGWindowListCopyWindowInfo(
                        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
                    )
                    for win in windows or []:
                        if win.get("kCGWindowOwnerPID") == pid and win.get("kCGWindowLayer") == 0:
                            bounds = win.get("kCGWindowBounds", {})
                            if bounds.get("Height", 0) > 80 and bounds.get("Width", 0) > 80:
                                front_win_id = win.get("kCGWindowNumber")
                                win_title = str(win.get("kCGWindowName") or "")
                                break
    except Exception as e:
        logger.debug(f"Error resolving window via Quartz: {e}")

    # Fallback to AppleScript on macOS
    if not win_title and sys.platform == "darwin":
        try:
            script = """
            tell application "System Events"
                tell (first application process whose frontmost is true)
                    try
                        return name of front window
                    end try
                end tell
            end tell
            """
            out = subprocess.check_output(["osascript", "-e", script], text=True, timeout=1.5).strip()
            if out and out != "missing value":
                win_title = out
        except Exception:
            pass

    # Fallback to Linux X11 xdotool
    if not win_title and not sys.platform == "darwin":
        try:
            wid_str = subprocess.check_output(["xdotool", "getactivewindow"], text=True, timeout=1.0, stderr=subprocess.DEVNULL).strip()
            if wid_str:
                front_win_id = wid_str
                win_title = subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"], text=True, timeout=1.0, stderr=subprocess.DEVNULL).strip()
                pid_str = subprocess.check_output(["xdotool", "getactivewindow", "getwindowpid"], text=True, timeout=1.0, stderr=subprocess.DEVNULL).strip()
                if pid_str:
                    comm_path = Path(f"/proc/{pid_str}/comm")
                    if comm_path.exists():
                        app_name = comm_path.read_text().strip()
        except Exception:
            pass

    display_title = f"{app_name} - {win_title}" if win_title else app_name

    # Capture window screenshot
    tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    ok = _capture_screen_to_file(tmp_png, front_win_id=front_win_id)
    if not ok:
        raise RuntimeError("Could not capture active window (screencapture/grim/maim/scrot/import not available or failed)")

    # Convert captured PNG into a high-contrast E-ink PDF
    import pymupdf as fitz
    from PIL import Image
    from quaderno_companion.config import SCREEN_PROFILES
    prof = SCREEN_PROFILES.get(target_profile, SCREEN_PROFILES["A4"])
    img = Image.open(tmp_png)
    img_w, img_h = img.size

    # Auto-rotate landscape window captures 90° clockwise so they align with Quaderno portrait screen
    if auto_rotate and img_w > img_h:
        img = img.transpose(Image.Transpose.ROTATE_270)
        img_w, img_h = img.size

    # Save rotated full uncropped image
    img.save(tmp_png)

    # Use standard ISO paper dimensions for Quaderno (A4 = 595x842 pt, A5 = 420x595 pt)
    paper_format = "a5" if target_profile.upper() == "A5" else "a4"
    pt_w, pt_h = fitz.paper_size(paper_format)

    # Create PDF page matching the full Quaderno A4/A5 screen
    doc = fitz.open()
    page = doc.new_page(width=pt_w, height=pt_h)

    # Insert uncropped image stretched to fill 100% of the canvas edge-to-edge on all 4 borders (0 margins)
    rect = fitz.Rect(0, 0, pt_w, pt_h)
    page.insert_image(rect, filename=tmp_png, keep_proportion=False)

    optimized_pdf = doc.tobytes(
        garbage=4,
        clean=True,
        deflate=True,
        deflate_images=True,
    )
    doc.close()
    img.close()

    # Save to user cache PDF file
    import os
    settings.ensure_directories()
    clean_slug = "".join(c if c.isalnum() else "_" for c in display_title[:30]).strip("_")
    filename = f"Window_{int(time.time())}_{clean_slug}.pdf"
    tmp_pdf = settings.cache_dir / filename
    tmp_pdf.write_bytes(optimized_pdf)
    try:
        os.chmod(tmp_pdf, 0o600)
    except Exception:
        pass

    # Clean up PNG
    try:
        Path(tmp_png).unlink()
    except Exception:
        pass

    logger.info(f"Captured active window '{display_title}' to {tmp_pdf}")
    return tmp_pdf, filename, display_title
