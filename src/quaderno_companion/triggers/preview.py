"""macOS Preview integration and native UI dialogs/accessibility bindings."""

import ctypes
import ctypes.util
import json
import logging
import re
import subprocess
from typing import Optional, Tuple

import sys

logger = logging.getLogger(__name__)


def _applescript_quote(s: str, max_len: Optional[int] = None) -> str:
    """Format string as a safe, escaped AppleScript string literal."""
    if not isinstance(s, str):
        s = str(s)
    clean = s.replace("\0", "")
    if max_len and len(clean) > max_len:
        clean = clean[:max_len]
    return json.dumps(clean)


def notify(title: str, subtitle: str, message: str):
    """Display native desktop notification across macOS (osascript) and Linux (notify-send)."""
    # macOS
    if sys.platform == "darwin":
        safe_title = _applescript_quote(title, 80)
        safe_subtitle = _applescript_quote(subtitle, 80)
        safe_msg = _applescript_quote(message, 200)
        script = f"display notification {safe_msg} with title {safe_title} subtitle {safe_subtitle}"
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            return
        except Exception:
            pass

    # Linux (notify-send)
    body = f"{subtitle}\n{message}" if subtitle else message
    try:
        subprocess.run(["notify-send", title, body], capture_output=True, check=False)
    except Exception:
        logger.info(f"[Notification] {title} - {body}")


def show_alert(title: str, message: str):
    """Display an inescapable foreground alert dialog on macOS or Linux."""
    # macOS
    if sys.platform == "darwin":
        safe_title = _applescript_quote(title, 100)
        safe_msg = _applescript_quote(message, 1000)
        script = f"""
        tell application "System Events"
            activate
            display alert {safe_title} message {safe_msg} as warning
        end tell
        """
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            return
        except Exception:
            pass

    # Linux (zenity / kdialog)
    try:
        subprocess.run(["zenity", "--warning", f"--title={title}", f"--text={message}"], capture_output=True, check=False)
        return
    except Exception:
        pass

    try:
        subprocess.run(["kdialog", "--sorry", message, f"--title={title}"], capture_output=True, check=False)
        return
    except Exception:
        pass

    logger.warning(f"[Alert] {title}: {message}")


def prompt_text_dialog(title: str, prompt: str, default_text: str = "") -> Optional[str]:
    """Display a native foreground input prompt dialog on macOS or Linux."""
    # macOS
    if sys.platform == "darwin":
        safe_title = _applescript_quote(title, 100)
        safe_prompt = _applescript_quote(prompt, 500)
        safe_default = _applescript_quote(default_text, 1000)

        script = f"""
        tell application "System Events"
            activate
            set res to display dialog {safe_prompt} default answer {safe_default} with title {safe_title} buttons {{"Cancel", "OK"}} default button "OK"
            return text returned of res
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return None

    # Linux (zenity / kdialog)
    try:
        res = subprocess.run(
            ["zenity", "--entry", f"--title={title}", f"--text={prompt}", f"--entry-text={default_text}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["kdialog", "--inputbox", prompt, default_text, f"--title={title}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass

    return None


def prompt_delete_previous_dialog(prev_title: str) -> bool:
    """Prompt user with native dialog whether to delete previously pushed document."""
    msg = f"Do you want to delete the previously pushed document from your Quaderno?\n\nPrevious Document:\n'{prev_title[:80]}'"

    # macOS
    if sys.platform == "darwin":
        safe_msg = _applescript_quote(msg, 500)
        safe_title = _applescript_quote("Quaderno Companion", 80)
        script = f"""
        tell application "System Events"
            activate
            set res to display dialog {safe_msg} with title {safe_title} buttons {{"Keep", "Delete"}} default button 1 cancel button 1
            return button returned of res
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            return res.returncode == 0 and "Delete" in res.stdout
        except Exception:
            return False

    # Linux (zenity / kdialog)
    try:
        res = subprocess.run(
            ["zenity", "--question", "--title=Quaderno Companion", f"--text={msg}", "--ok-label=Delete", "--cancel-label=Keep"],
            capture_output=True,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        pass

    return False


# Setup C-level macOS ApplicationServices & CoreFoundation for sub-millisecond Accessibility extraction
_app_services = None
_core_foundation = None
if sys.platform == "darwin":
    try:
        _as_path = ctypes.util.find_library("ApplicationServices")
        _cf_path = ctypes.util.find_library("CoreFoundation")
        if _as_path and _cf_path:
            _app_services = ctypes.cdll.LoadLibrary(_as_path)
            _core_foundation = ctypes.cdll.LoadLibrary(_cf_path)
            if _core_foundation and _app_services:
                _core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
                _core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
                _core_foundation.CFArrayGetCount.restype = ctypes.c_long
                _core_foundation.CFArrayGetCount.argtypes = [ctypes.c_void_p]
                _core_foundation.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
                _core_foundation.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
                _core_foundation.CFStringGetCString.restype = ctypes.c_bool
                _core_foundation.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
                _core_foundation.CFGetTypeID.restype = ctypes.c_long
                _core_foundation.CFGetTypeID.argtypes = [ctypes.c_void_p]
                _core_foundation.CFStringGetTypeID.restype = ctypes.c_long

                _app_services.AXUIElementCreateApplication.restype = ctypes.c_void_p
                _app_services.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
                _app_services.AXUIElementCopyAttributeValue.restype = ctypes.c_int
                _app_services.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    except Exception:
        _app_services = None
        _core_foundation = None


def _cf_str(s: str):
    if not _core_foundation:
        return None
    return _core_foundation.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)


def _get_cf_str_value(cf_val) -> Optional[str]:
    if not cf_val or not _core_foundation:
        return None
    try:
        if _core_foundation.CFGetTypeID(cf_val) == _core_foundation.CFStringGetTypeID():
            buf = ctypes.create_string_buffer(512)
            if _core_foundation.CFStringGetCString(cf_val, buf, 512, 0x08000100):
                return buf.value.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None


def get_preview_current_page_native() -> int:
    """Read the active page number from Apple Preview via macOS Accessibility C API."""
    if _app_services is None or _core_foundation is None:
        return 1
    app_srv = _app_services
    cf = _core_foundation
    try:
        pids = subprocess.check_output(["pgrep", "-x", "Preview"], text=True).strip().split("\n")
        if not pids or not pids[0]:
            return 1
        pid = int(pids[0])
        app_elem = app_srv.AXUIElementCreateApplication(pid)
        if not app_elem:
            return 1

        win_ref = ctypes.c_void_p()
        err = app_srv.AXUIElementCopyAttributeValue(app_elem, _cf_str("AXMainWindow"), ctypes.byref(win_ref))
        if err != 0 or not win_ref.value:
            err = app_srv.AXUIElementCopyAttributeValue(app_elem, _cf_str("AXFocusedWindow"), ctypes.byref(win_ref))
        if err != 0 or not win_ref.value:
            return 1

        main_win = win_ref.value

        def find_page_in_elem(elem, depth=0):
            if depth > 6 or not elem:
                return None
            val_ref = ctypes.c_void_p()
            app_srv.AXUIElementCopyAttributeValue(elem, _cf_str("AXValue"), ctypes.byref(val_ref))
            val = _get_cf_str_value(val_ref.value)
            if val:
                m = re.search(r"Page\s+(\d+)\s+of", val, re.IGNORECASE)
                if m:
                    return int(m.group(1))

            children_ref = ctypes.c_void_p()
            err = app_srv.AXUIElementCopyAttributeValue(elem, _cf_str("AXChildren"), ctypes.byref(children_ref))
            if err == 0 and children_ref.value:
                cnt = cf.CFArrayGetCount(children_ref.value)
                for i in range(cnt):
                    child = cf.CFArrayGetValueAtIndex(children_ref.value, i)
                    res = find_page_in_elem(child, depth + 1)
                    if res:
                        return res
            return None

        p = find_page_in_elem(main_win)
        return p if p else 1
    except Exception:
        return 1


def get_preview_document_info() -> Tuple[Optional[str], int]:
    """Retrieve POSIX path and current active page number from Apple Preview.

    Returns:
        (file_path, page_number) where page_number defaults to 1 if undetected.
    """
    doc_path = None
    script = '''
    tell application "Preview"
        try
            if (count of documents) > 0 then
                return path of front document
            end if
        end try
    end tell
    return ""
    '''
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2, check=False)
        if res.returncode == 0 and res.stdout.strip():
            doc_path = res.stdout.strip()
    except Exception:
        pass

    if not doc_path:
        return None, 1

    page_num = get_preview_current_page_native()
    return doc_path, page_num


def get_preview_document_path() -> Optional[str]:
    """Compatibility alias returning only the document path."""
    path, _ = get_preview_document_info()
    return path
