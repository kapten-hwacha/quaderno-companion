"""Native macOS active browser tab detector (Zero-Extension Firefox, Safari, Chrome, Arc, Brave)."""

import json
import logging
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional
import lz4.block

try:
    import AppKit as _AppKit  # type: ignore[import-untyped]
    AppKit: Any = _AppKit
except Exception:
    AppKit = None

logger = logging.getLogger(__name__)

MOZ_HEADER = b"mozLz40\0"


def _decompress_mozlz4(data: bytes) -> Optional[dict]:
    """Decompress Mozilla's proprietary LZ4-compressed JSON session file."""
    if not data.startswith(MOZ_HEADER):
        return None
    try:
        uncompressed = lz4.block.decompress(data[len(MOZ_HEADER):])
        return json.loads(uncompressed.decode("utf-8"))
    except Exception as e:
        logger.debug(f"Failed to decompress mozlz4 data: {e}")
        return None


def get_firefox_profile_dirs() -> List[Path]:
    """Find Firefox profile directories across Linux (native, Flatpak, Snap) and macOS."""
    candidates = [
        # macOS
        Path.home() / "Library/Application Support/Firefox/Profiles",
        # Linux Native / Distro
        Path.home() / ".mozilla/firefox",
        # Linux Flatpak
        Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox",
        # Linux Snap
        Path.home() / "snap/firefox/common/.mozilla/firefox",
        # Librewolf / Waterfox Linux
        Path.home() / ".librewolf",
        Path.home() / ".waterfox",
    ]
    dirs = []
    for c in candidates:
        if c.exists():
            dirs.append(c)
    return dirs


def get_firefox_window_title() -> Optional[str]:
    """Get the active window title of Firefox via AppleScript (macOS) or xdotool/xprop (Linux)."""
    # macOS AppleScript
    if sys.platform == "darwin":
        script = '''
        tell application "System Events"
            if exists (process "Firefox") then
                tell process "Firefox"
                    try
                        return name of front window
                    end try
                end tell
            end if
        end tell
        '''
        try:
            res = subprocess.check_output(["osascript", "-e", script], text=True, timeout=1.5).strip()
            if res and res != "missing value":
                return res
        except Exception:
            pass

    # Linux X11 window title via xdotool
    try:
        res = subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"], text=True, timeout=1.0, stderr=subprocess.DEVNULL).strip()
        if res:
            return res
    except Exception:
        pass

    return None


def get_firefox_active_tab() -> Optional[Dict[str, str]]:
    """Extract real-time active tab title and URL directly from Firefox with 0ms latency."""
    profile_dirs = get_firefox_profile_dirs()
    if not profile_dirs:
        return None

    # Method 1: Instantaneous Real-time History Database (places.sqlite)
    db_profiles = []
    for ff_dir in profile_dirs:
        try:
            db_profiles.extend([p for p in ff_dir.iterdir() if p.is_dir() and (p / "places.sqlite").is_file()])
        except Exception:
            pass

    if db_profiles:
        latest_profile = max(db_profiles, key=lambda p: (p / "places.sqlite").stat().st_mtime)
        db_file = latest_profile / "places.sqlite"
        try:
            # Query read-only immutable SQLite connection
            query = """
            SELECT p.url, p.title
            FROM moz_places p
            JOIN moz_historyvisits v ON p.id = v.place_id
            WHERE p.url NOT LIKE 'about:%' AND p.url NOT LIKE 'moz-extension:%'
            ORDER BY v.visit_date DESC
            LIMIT 1
            """
            try:
                conn = sqlite3.connect(f"file:{db_file}?mode=ro&immutable=1", uri=True)
                c = conn.cursor()
                c.execute(query)
                row = c.fetchone()
                conn.close()
            except Exception:
                with tempfile.NamedTemporaryFile(suffix=".sqlite") as tf:
                    shutil.copyfile(db_file, tf.name)
                    conn = sqlite3.connect(tf.name)
                    c = conn.cursor()
                    c.execute(query)
                    row = c.fetchone()
                    conn.close()

            if row and row[0]:
                url, title = row[0], row[1]
                win_title = get_firefox_window_title()
                clean_title = win_title or title or url
                # Remove trailing " — Mozilla Firefox" or " | Firefox" if present
                for suffix in [" — Mozilla Firefox", " - Mozilla Firefox", " | Mozilla Firefox"]:
                    if clean_title.endswith(suffix):
                        clean_title = clean_title[:-len(suffix)].strip()
                return {
                    "browser": "Firefox",
                    "title": clean_title,
                    "url": url,
                }
        except Exception as e:
            logger.debug(f"Error querying Firefox places.sqlite: {e}")

    # Method 2: Fallback to Session Store recovery.jsonlz4
    recovery_files: List[Path] = []
    for ff_dir in profile_dirs:
        for pattern in [
            "*/sessionstore-backups/recovery.jsonlz4",
            "*/sessionstore-backups/recovery.baklz4",
            "*/sessionstore.jsonlz4",
            "sessionstore-backups/recovery.jsonlz4",
            "sessionstore-backups/recovery.baklz4",
            "sessionstore.jsonlz4",
        ]:
            recovery_files.extend(ff_dir.glob(pattern))

    if not recovery_files:
        return None

    latest_file = max(recovery_files, key=lambda f: f.stat().st_mtime)
    try:
        data = latest_file.read_bytes()
        state = _decompress_mozlz4(data)
        if not state:
            return None

        windows = state.get("windows", [])
        for win in windows:
            selected_tab_idx = win.get("selected", 1) - 1
            tabs = win.get("tabs", [])
            if 0 <= selected_tab_idx < len(tabs):
                active_tab = tabs[selected_tab_idx]
                entries = active_tab.get("entries", [])
                if entries:
                    cur_idx = active_tab.get("index", len(entries)) - 1
                    cur_entry = entries[cur_idx] if 0 <= cur_idx < len(entries) else entries[-1]
                    url = cur_entry.get("url", "")
                    title = cur_entry.get("title") or url
                    if url and not url.startswith("about:"):
                        return {
                            "browser": "Firefox",
                            "title": title,
                            "url": url,
                        }
    except Exception as e:
        logger.debug(f"Error reading Firefox session store: {e}")
    return None


def get_safari_active_tab() -> Optional[Dict[str, str]]:
    """Extract active tab title and URL from Safari via AppleScript."""
    script = """
    tell application "System Events"
        if exists (process "Safari") then
            tell application "Safari"
                if (count of windows) > 0 then
                    return {name of current tab of front window, URL of current tab of front window}
                end if
            end tell
        end if
    end tell
    """
    try:
        res = subprocess.check_output(["osascript", "-e", script], text=True, timeout=2).strip()
        if res and ", " in res:
            parts = res.split(", ", 1)
            title, url = parts[0], parts[1]
            if url and url != "missing value" and not url.startswith("favorites://"):
                return {"browser": "Safari", "title": title, "url": url}
    except Exception:
        pass
    return None


def get_chromium_active_tab(app_name: str = "Google Chrome") -> Optional[Dict[str, str]]:
    """Extract active tab title and URL from Chromium-based browsers (Chrome, Arc, Brave, Edge)."""
    script = f"""
    tell application "System Events"
        if exists (process "{app_name}") then
            tell application "{app_name}"
                if (count of windows) > 0 then
                    return {{title of active tab of front window, URL of active tab of front window}}
                end if
            end tell
        end if
    end tell
    """
    try:
        res = subprocess.check_output(["osascript", "-e", script], text=True, timeout=2).strip()
        if res and ", " in res:
            parts = res.split(", ", 1)
            title, url = parts[0], parts[1]
            if url and url != "missing value" and not url.startswith("chrome://"):
                return {"browser": app_name, "title": title, "url": url}
    except Exception:
        pass
    return None


def get_frontmost_app_name() -> str:
    """Return the process/app name of the current frontmost application in ~0.05ms."""
    # macOS AppKit
    try:
        if AppKit:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                return str(app.localizedName() or "")
    except Exception:
        pass

    # macOS AppleScript fallback
    if sys.platform == "darwin":
        script = 'tell application "System Events" to return name of first application process whose frontmost is true'
        try:
            return subprocess.check_output(["osascript", "-e", script], text=True, timeout=1.5).strip()
        except Exception:
            pass

    # Linux X11 fallback (xdotool / /proc)
    try:
        pid_str = subprocess.check_output(["xdotool", "getactivewindow", "getwindowpid"], text=True, timeout=1.0, stderr=subprocess.DEVNULL).strip()
        if pid_str:
            comm_path = Path(f"/proc/{pid_str}/comm")
            if comm_path.exists():
                return comm_path.read_text().strip()
    except Exception:
        pass

    return ""


def get_active_browser_tab() -> Optional[Dict[str, str]]:
    """Universal active browser tab detector across Firefox, Safari, Chrome, Arc, Brave, and Edge."""
    front_app = get_frontmost_app_name().lower()

    # 1. If frontmost app is Firefox
    if "firefox" in front_app:
        tab = get_firefox_active_tab()
        if tab:
            return tab

    # 2. If frontmost app is Safari
    if "safari" in front_app:
        tab = get_safari_active_tab()
        if tab:
            return tab

    # 3. If frontmost app is Chrome, Arc, Brave, Edge
    for chrom_name in ["Google Chrome", "Arc", "Brave Browser", "Microsoft Edge"]:
        if chrom_name.lower() in front_app:
            tab = get_chromium_active_tab(chrom_name)
            if tab:
                return tab

    # 4. Fallback: Query browsers in order of active presence
    ff_tab = get_firefox_active_tab()
    if ff_tab:
        return ff_tab

    safari_tab = get_safari_active_tab()
    if safari_tab:
        return safari_tab

    for chrom_name in ["Google Chrome", "Arc", "Brave Browser", "Microsoft Edge"]:
        chrom_tab = get_chromium_active_tab(chrom_name)
        if chrom_tab:
            return chrom_tab

    return None
