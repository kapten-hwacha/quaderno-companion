"""Native macOS Menu Bar Background Application for Quaderno Companion.

Provides seamless system-wide desktop integration:
- Real-time battery and reading state in macOS menu bar
- Push URL/text directly from Clipboard
- Native File Picker to push local PDFs/documents
- Fast Page Navigation controls (Next/Prev)
- Embedded background FastAPI daemon runner
"""

import asyncio
import logging
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

import sys
import uvicorn

logger = logging.getLogger(__name__)

rumps: Any
AppKit: Any
objc: Any

try:
    import rumps as _rumps  # type: ignore[import-untyped]
    rumps = _rumps
except Exception:
    class _DummyRumps:
        class MenuItem:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass
            def add(self, *args: Any, **kwargs: Any) -> None:
                pass
            def clear(self) -> None:
                pass
        class App:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.menu: Any = []
            def run(self) -> None:
                pass
        class Timer:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass
            def start(self) -> None: pass
            def stop(self) -> None: pass
        @staticmethod
        def quit_application() -> None: pass
    rumps = _DummyRumps()

from quaderno_companion.agent.core import agent
from quaderno_companion.agent.tools import (
    tool_navigate_reader,
    tool_push_document,
)
from quaderno_companion.config import settings
from quaderno_companion.device.manager import device_manager
from quaderno_companion.triggers.preview import (
    get_preview_document_info,
    notify,
    prompt_delete_previous_dialog,
    prompt_folder_dialog,
    prompt_text_dialog,
    show_alert,
)

try:
    import AppKit as _AppKit  # type: ignore[import-untyped]
    import objc as _objc  # type: ignore[import-untyped]
    AppKit = _AppKit
    objc = _objc
except Exception:
    class _DummyAppKit:
        NSObject = object
        NSView = object
        NSMenuItem = object
        NSSegmentedControl = object
        NSSlider = object
        NSTextField = object
        NSSwitch = object
        NSApplication = object
        NSEvent = object
        NSFont = object
        NSColor = object
        NSControlStateValueOn = 1
        NSControlStateValueOff = 0
        NSSegmentSwitchTrackingMomentary = 0
        NSSegmentSwitchTrackingSelectOne = 1
        NSTickMarkPositionBelow = 0
        NSFontWeightMedium = 0
        NSTextAlignmentLeft = 0
        NSTextAlignmentRight = 1
        NSLineBreakByClipping = 0
        NSEventTypeLeftMouseUp = 2
        @staticmethod
        def NSMakeRect(*args: Any) -> Any: return None
        @staticmethod
        def NSPointInRect(*args: Any) -> bool: return False

    class _DummyObjc:
        @staticmethod
        def selector(*args: Any, **kwargs: Any) -> Any:
            def decorator(f: Any) -> Any: return f
            return decorator

        @staticmethod
        def super(cls: Any, inst: Any) -> Any:
            return super(cls, inst)

    AppKit = _DummyAppKit()
    objc = _DummyObjc()

MenuItemBase: Any = rumps.MenuItem
AppBase: Any = rumps.App


class BackgroundAsyncWorker:
    """Persistent background thread managing a single shared asyncio event loop."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            ready = threading.Event()

            def _run():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                ready.set()
                self._loop.run_forever()

            self._thread = threading.Thread(target=_run, daemon=True, name="MenubarAsyncWorker")
            self._thread.start()
            ready.wait()

    def stop(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=1.0)
            self._loop = None
            self._thread = None

    def submit(self, coro):
        """Submit a coroutine to the background event loop."""
        if not self._loop or not self._loop.is_running():
            self.start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)


bg_worker = BackgroundAsyncWorker()


class ToggleMenuItem(MenuItemBase):
    """MenuItem that retains custom switch state without triggering macOS checkmark gutter shifts."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._switch_ctrl = None
        self._custom_state: bool = False

    @property
    def state(self) -> bool:
        if self._switch_ctrl is not None and AppKit is not None:
            try:
                return bool(self._switch_ctrl.state() == AppKit.NSControlStateValueOn)
            except Exception:
                pass
        return self._custom_state

    @state.setter
    def state(self, val: bool):
        self._custom_state = bool(val)
        if self._switch_ctrl is not None and AppKit is not None:
            try:
                self._switch_ctrl.setState_(
                    AppKit.NSControlStateValueOn if self._custom_state else AppKit.NSControlStateValueOff
                )
            except Exception:
                pass


class QuadernoMenubarApp(AppBase):
    """macOS status bar companion application."""

    def __init__(self):
        super().__init__("📖 Quaderno", quit_button=None)
        
        # Menu Structure
        self.status_item = rumps.MenuItem("Status: Checking...")
        self.doc_item = rumps.MenuItem("Active Document: None")
        self.battery_item = rumps.MenuItem("Battery: -")
        self.storage_item = rumps.MenuItem("Storage: -")

        try:
            if not AppKit or not objc or type(AppKit).__name__ == "_DummyAppKit":
                raise ImportError("PyObjC / AppKit is unavailable")

            # Native NSSlider Page Scroller / Scrollbar with Live Page Badge and Chapter Snapping
            class _PageSliderHandler(AppKit.NSObject):
                def initWithApp_(self, app_inst):
                    self = objc.super(_PageSliderHandler, self).init()
                    if self is not None:
                        self.app = app_inst
                        self._debounce_timer = None
                        self._pending_page = 1
                    return self

                def handleSlider_(self, sender):
                    import time, threading
                    raw_val = sender.doubleValue()
                    tot = 1
                    title_short = "Document"
                    if hasattr(self.app, "_last_reading_state") and self.app._last_reading_state:
                        tot = max(1, self.app._last_reading_state.total_pages)
                        title_str = self.app._last_reading_state.title or "Document"
                        title_short = title_str[:24] + ("..." if len(title_str) > 24 else "")

                    target_page, chapter_title, is_clipped = self.app._get_clipped_chapter(raw_val, tot)

                    # When clipped to a chapter, magnetically snap the slider knob
                    if is_clipped:
                        sender.setDoubleValue_(float(target_page))

                    self._pending_page = target_page
                    self.app._last_user_nav_time = time.time()

                    # Update live page badge immediately at 60fps while dragging
                    if hasattr(self.app, "slider_page_badge") and self.app.slider_page_badge is not None:
                        self.app.slider_page_badge.setStringValue_(f"p. {target_page} / {tot}")

                    # Update chapter title banner immediately
                    if hasattr(self.app, "slider_chapter_label") and self.app.slider_chapter_label is not None:
                        if chapter_title:
                            self.app.slider_chapter_label.setStringValue_(f"📑 {chapter_title}")
                            self.app.slider_chapter_label.setTextColor_(AppKit.NSColor.labelColor())
                        else:
                            self.app.slider_chapter_label.setStringValue_("")

                    self.app.doc_item.title = f"📖 {title_short} ({target_page}/{tot})"

                    # Cancel pending timer
                    if self._debounce_timer is not None:
                        try:
                            self._debounce_timer.cancel()
                        except Exception:
                            pass
                        self._debounce_timer = None

                    # Check if user let go of the mouse button
                    try:
                        app_obj = AppKit.NSApplication.sharedApplication()
                        event = app_obj.currentEvent() if app_obj else None
                        is_mouse_up = event is not None and event.type() == AppKit.NSEventTypeLeftMouseUp
                    except Exception:
                        is_mouse_up = False

                    if is_mouse_up:
                        self.app._async_nav("goto", page=target_page)
                    else:
                        # Fallback trailing debounce: dispatch when mouse settles/releases
                        def _deferred_dispatch():
                            self.app._async_nav("goto", page=self._pending_page)
                        self._debounce_timer = threading.Timer(0.35, _deferred_dispatch)
                        self._debounce_timer.daemon = True
                        self._debounce_timer.start()

            self._slider_handler = _PageSliderHandler.alloc().initWithApp_(self)

            # Top Chapter Banner Label
            self.slider_chapter_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(14, 22, 200, 16))
            self.slider_chapter_label.setStringValue_("")
            self.slider_chapter_label.setBezeled_(False)
            self.slider_chapter_label.setDrawsBackground_(False)
            self.slider_chapter_label.setEditable_(False)
            self.slider_chapter_label.setSelectable_(False)
            self.slider_chapter_label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(11.0, AppKit.NSFontWeightMedium))
            self.slider_chapter_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            self.slider_chapter_label.setAlignment_(AppKit.NSTextAlignmentLeft)
            if hasattr(self.slider_chapter_label, "cell") and self.slider_chapter_label.cell() is not None:
                self.slider_chapter_label.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)

            # Left min page label (1)
            self.slider_min_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(14, 2, 14, 16))
            self.slider_min_label.setStringValue_("1")
            self.slider_min_label.setBezeled_(False)
            self.slider_min_label.setDrawsBackground_(False)
            self.slider_min_label.setEditable_(False)
            self.slider_min_label.setSelectable_(False)
            self.slider_min_label.setFont_(AppKit.NSFont.systemFontOfSize_(10.0))
            self.slider_min_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            self.slider_min_label.setAlignment_(AppKit.NSTextAlignmentLeft)

            # Center Slider with smooth continuous tracking (no visible tick marks)
            self.page_slider = AppKit.NSSlider.alloc().initWithFrame_(AppKit.NSMakeRect(28, 1, 134, 18))
            self.page_slider.setMinValue_(1.0)
            self.page_slider.setMaxValue_(1.0)
            self.page_slider.setDoubleValue_(1.0)
            self.page_slider.setContinuous_(True)
            self.page_slider.setAllowsTickMarkValuesOnly_(False)
            self.page_slider.setNumberOfTickMarks_(0)
            self.page_slider.setTarget_(self._slider_handler)
            self.page_slider.setAction_(objc.selector(self._slider_handler.handleSlider_, signature=b"v@:@"))

            # Right live page badge (p. 4 / 12)
            self.slider_page_badge = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(164, 2, 60, 16))
            self.slider_page_badge.setStringValue_("p. 1 / 1")
            self.slider_page_badge.setBezeled_(False)
            self.slider_page_badge.setDrawsBackground_(False)
            self.slider_page_badge.setEditable_(False)
            self.slider_page_badge.setSelectable_(False)
            self.slider_page_badge.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(10.0, AppKit.NSFontWeightMedium))
            self.slider_page_badge.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            self.slider_page_badge.setAlignment_(AppKit.NSTextAlignmentRight)

            slider_container = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 228, 40))
            slider_container.addSubview_(self.slider_chapter_label)
            slider_container.addSubview_(self.slider_min_label)
            slider_container.addSubview_(self.page_slider)
            slider_container.addSubview_(self.slider_page_badge)
            self.page_slider_item = rumps.MenuItem("")
            self.page_slider_item._menuitem.setView_(slider_container)

            # Native NSSwitch Checkbox / Toggle Mode Rows (Won't dismiss menu when toggled)
            class _ToggleSwitchHandler(AppKit.NSObject):
                def initWithApp_key_(self, app_inst, key_name):
                    self = objc.super(_ToggleSwitchHandler, self).init()
                    if self is not None:
                        self.app = app_inst
                        self.key_name = key_name
                    return self

                def handleToggle_(self, sender):
                    is_on = bool(sender.state() == AppKit.NSControlStateValueOn)
                    if self.key_name == "watch":
                        self.app.watch_mode_item.state = is_on
                        if is_on:
                            self.app._last_synced_doc = None
                            self.app._last_synced_page = 1
                            notify("Quaderno Companion", "Preview Mirror Enabled", "Turning pages in Preview will now mirror to Quaderno.")
                            self.app._check_live_preview_sync()
                        else:
                            notify("Quaderno Companion", "Preview Mirror Disabled", "Automatic page mirroring stopped.")

            class _ToggleRowView(AppKit.NSView):
                def initWithFrame_switch_handler_(self, frame, sw, handler):
                    self = objc.super(_ToggleRowView, self).initWithFrame_(frame)
                    if self is not None:
                        self.sw = sw
                        self.handler = handler
                    return self

                def hitTest_(self, point):
                    if hasattr(self, "sw") and self.sw is not None:
                        if AppKit.NSPointInRect(point, self.sw.frame()):
                            return self.sw
                    return self

                def mouseUp_(self, event):
                    new_val = AppKit.NSControlStateValueOff if self.sw.state() == AppKit.NSControlStateValueOn else AppKit.NSControlStateValueOn
                    self.sw.setState_(new_val)
                    self.handler.handleToggle_(self.sw)

            def _create_switch_item(title_text: str, key_name: str):
                handler = _ToggleSwitchHandler.alloc().initWithApp_key_(self, key_name)
                sw = AppKit.NSSwitch.alloc().initWithFrame_(AppKit.NSMakeRect(174, 3, 38, 20))
                sw.setState_(AppKit.NSControlStateValueOff)
                sw.setTarget_(handler)
                sw.setAction_(objc.selector(handler.handleToggle_, signature=b"v@:@"))

                container = _ToggleRowView.alloc().initWithFrame_switch_handler_(AppKit.NSMakeRect(0, 0, 225, 26), sw, handler)

                label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(18, 4, 150, 18))
                label.setStringValue_(title_text)
                label.setBezeled_(False)
                label.setDrawsBackground_(False)
                label.setEditable_(False)
                label.setSelectable_(False)
                label.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
                label.setTextColor_(AppKit.NSColor.labelColor())
                label.setAlignment_(AppKit.NSTextAlignmentLeft)

                container.addSubview_(label)
                container.addSubview_(sw)

                item = ToggleMenuItem("")
                item._switch_ctrl = sw
                item._menuitem.setView_(container)
                item.state = False
                return item, sw, handler

            self.watch_mode_item, self.watch_switch, self._watch_handler_inst = _create_switch_item("🪞 Preview Mirror", "watch")
        except Exception:
            self.page_slider_item = rumps.MenuItem("")
            self.slider_chapter_label = None
            self.slider_min_label = None
            self.slider_page_badge = None
            self.page_slider = None
            self.watch_mode_item = rumps.MenuItem("🪞 Preview Mirror", callback=self.toggle_watch_mode)
            self.watch_mode_item.state = False
            self.watch_switch = None
        
        # Chapters tracking list for unified slider clipping
        self._current_chapters: List[Tuple[str, int]] = []
        self._last_loaded_toc_sig = None

        # Dropdown Submenu to Jump Between Chapters (maintained for compatibility)
        self.chapters_menu = rumps.MenuItem("📑 Jump to Chapter")
        self.chapters_menu.add(rumps.MenuItem("No active document"))

        self.sync_now_item = rumps.MenuItem("🔄 Sync Now", callback=self.trigger_sync_now)
        self.open_folder_item = rumps.MenuItem("📁 Open Quaderno Folder", callback=self.open_quaderno_folder)

        self._last_user_nav_time: float = 0.0
        self._last_reading_state = None
        self._last_synced_doc: Optional[str] = None
        self._last_synced_page: int = 1
        self._sync_in_progress = False
        self._telemetry_in_progress = False

        self.push_file_item = rumps.MenuItem("📁 Push Local File...", callback=self.choose_and_push_file)
        self.other_push_menu = rumps.MenuItem("Other Push Options")
        self.other_push_menu.add(rumps.MenuItem("🌐 Push Active Browser Tab", callback=self.push_browser_tab))
        self.other_push_menu.add(rumps.MenuItem("🖥️ Push Active Window", callback=self.push_active_window))
        self.other_push_menu.add(rumps.MenuItem("📋 Push from Clipboard", callback=self.push_clipboard))
        self.other_push_menu.add(rumps.MenuItem("👁️ Push from Preview", callback=self.push_preview))
        self.other_push_menu.add(rumps.MenuItem("🔗 Push URL...", callback=self.push_url_dialog))

        self.menu = [
            self.doc_item,
            self.page_slider_item,
            self.watch_mode_item,
            None,  # Separator (unified scroller handles navigation and chapters)
            self.push_file_item,
            self.open_folder_item,
            self.other_push_menu,
            None,  # Separator
            self.status_item,
            self.battery_item,
            self.storage_item,
            self.sync_now_item,
            None,  # Separator
            rumps.MenuItem("Quit Quaderno Companion", callback=self.quit_app),
        ]

        # Timer for polling device status and live sync (every 10 seconds / 0.1 Hz)
        self.timer = rumps.Timer(self.on_tick, settings.telemetry_poll_interval)
        self.timer.start()

    def trigger_sync_now(self, _):
        """Run an immediate background folder sync pass."""
        def _sync_worker():
            notify("Quaderno Companion", "Syncing...", "Synchronizing folder mirror with Quaderno...")
            try:
                from quaderno_companion.fs.syncer import syncer
                res = syncer.sync_pass()
                if res.errors:
                    notify("Quaderno Companion", "Sync Error", f"Errors: {res.errors[0]}")
                else:
                    msg = f"Pulled {len(res.pulled)}, Pushed {len(res.pushed)}, Deleted {len(res.deleted)}"
                    notify("Quaderno Companion", "Sync Complete", msg)
            except Exception as e:
                show_alert("Sync Error", f"Sync failed: {e}")

        threading.Thread(target=_sync_worker, daemon=True).start()

    def open_quaderno_folder(self, _):
        """Open the local Quaderno mirror directory in Finder."""
        import subprocess
        settings.sync_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(settings.sync_dir)], check=False)

    def quit_app(self, _):
        """Clean up background tasks and quit companion."""
        try:
            from quaderno_companion.fs.syncer import sync_runner
            sync_runner.stop()
        except Exception:
            pass
        try:
            bg_worker.stop()
        except Exception:
            pass
        rumps.quit_application()

    def toggle_watch_mode(self, sender=None):
        """Toggle live Preview mirror on/off."""
        new_state = not bool(self.watch_mode_item.state)
        self.watch_mode_item.state = new_state
        if new_state:
            self._last_synced_doc = None
            self._last_synced_page = 1
            notify("Quaderno Companion", "Preview Mirror Enabled", "Turning pages in Preview will now mirror to Quaderno.")
            self._check_live_preview_sync()
        else:
            notify("Quaderno Companion", "Preview Mirror Disabled", "Automatic page mirroring stopped.")

    def on_tick(self, _=None):
        """Periodic tick handler for telemetry and live watch sync."""
        self.refresh_telemetry()
        if self.watch_mode_item.state:
            self._check_live_preview_sync()

    def _check_live_preview_sync(self):
        """Check if Preview page or document changed and synchronize."""
        if self._sync_in_progress:
            return

        async def _sync_coro():
            self._sync_in_progress = True
            try:
                doc_path, page_num = get_preview_document_info()
                if doc_path and (doc_path != self._last_synced_doc or page_num != self._last_synced_page):
                    if doc_path != self._last_synced_doc:
                        await tool_push_document(
                            source_url_or_path=doc_path,
                            title=Path(doc_path).stem,
                            page=page_num,
                        )
                    elif page_num != self._last_synced_page:
                        await tool_navigate_reader(action="goto", page=page_num)
                    self._last_synced_doc = doc_path
                    self._last_synced_page = page_num
            except Exception as e:
                logger.error(f"Live preview sync error: {e}", exc_info=True)
            finally:
                self._sync_in_progress = False

        return bg_worker.submit(_sync_coro())

    def _dispatch_to_main(self, fn):
        """Safely execute a UI update callback on the AppKit main thread."""
        if threading.current_thread() is threading.main_thread() or os.environ.get("CI") or "pytest" in sys.modules:
            try:
                fn()
                return
            except Exception:
                pass
        if AppKit is not None:
            try:
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
                return
            except Exception:
                pass
    def _get_clipped_chapter(self, target_val: float, total_pages: int) -> Tuple[int, Optional[str], bool]:
        """Check if continuous slider target value clips/snaps to any chapter within magnetic snap distance.
        Returns: (snapped_page, chapter_title_if_clipped, is_clipped)
        """
        chapters = getattr(self, "_current_chapters", [])
        if not chapters:
            target_page = max(1, min(total_pages, int(round(target_val))))
            return target_page, None, False

        snap_threshold = max(1.0, min(5.0, total_pages * 0.025))
        best_ch = None
        min_dist = float("inf")
        for ch_title, ch_page in chapters:
            dist = abs(target_val - ch_page)
            if dist < min_dist:
                min_dist = dist
                best_ch = (ch_title, ch_page)

        if best_ch is not None and min_dist <= snap_threshold:
            return best_ch[1], best_ch[0], True
        else:
            target_page = max(1, min(total_pages, int(round(target_val))))
            return target_page, None, False

    def _get_chapter_title_for_page(self, page: int) -> Optional[str]:
        """Return the chapter title if the given page matches a chapter start boundary, else None."""
        chapters = getattr(self, "_current_chapters", [])
        for ch_title, ch_page in chapters:
            if ch_page == page:
                return ch_title
        return None

    def refresh_telemetry(self, _=None):
        """Update menubar title and telemetry items in background."""
        if getattr(self, "_telemetry_in_progress", False):
            return

        async def _fetch_coro():
            self._telemetry_in_progress = True
            try:
                status = await device_manager.get_status()
                toc = []
                if status.is_connected and status.reading_state and status.reading_state.document_id:
                    doc_id = status.reading_state.document_id
                    try:
                        toc = await device_manager.get_toc(doc_id)
                    except Exception as toc_err:
                        logger.debug(f"Could not load TOC during telemetry refresh: {toc_err}")
                        toc = getattr(device_manager, "_doc_toc_cache", {}).get(doc_id, [])

                def _apply_ui():
                    if status.is_connected:
                        bat_str = f"{status.battery_level}%" if status.battery_level is not None else ""
                        self.title = f"📖 {bat_str}".strip()
                        self.status_item.title = f"✓ Connected ({status.connection_type})"

                        bat_full = f"Battery: {status.battery_level}%" if status.battery_level is not None else "Battery: N/A"
                        if status.battery_charging:
                            bat_full += " ⚡"
                        self.battery_item.title = bat_full

                        if status.storage_free_mb:
                            self.storage_item.title = f"Storage: {round(status.storage_free_mb/1024, 1)} GB free"
                        
                        if status.reading_state.title:
                            import time
                            self._last_reading_state = status.reading_state
                            title_short = status.reading_state.title[:24] + ("..." if len(status.reading_state.title) > 24 else "")
                            
                            # Prevent background poller from pulling slider or title back during user navigation
                            is_recent_nav = (time.time() - getattr(self, "_last_user_nav_time", 0.0)) < 3.0
                            tot = max(1, status.reading_state.total_pages)
                            cur = max(1, min(status.reading_state.current_page, tot))

                            if hasattr(self, "page_slider") and self.page_slider is not None:
                                self.page_slider.setMinValue_(1.0)
                                self.page_slider.setMaxValue_(float(tot))
                                self.page_slider.setNumberOfTickMarks_(0)
                                self.page_slider.setAllowsTickMarkValuesOnly_(False)
                                self.page_slider.setEnabled_(tot > 1)

                            # Dynamically populate Chapters Dropdown Submenu and unified slider chapters
                            doc_id = status.reading_state.document_id
                            tot_p_val = max(1, status.reading_state.total_pages)
                            toc_sig = (doc_id, len(toc) if toc else 0)
                            if doc_id and toc_sig != getattr(self, "_last_loaded_toc_sig", None):
                                self._last_loaded_toc_sig = toc_sig
                                self.chapters_menu.clear()
                                if toc:
                                    self._current_chapters = [(ch_title, ch_page) for ch_title, ch_page in toc]
                                    for ch_title, ch_page in toc[:40]:
                                        lbl = f"📑 {ch_title[:32]} (p. {ch_page})"
                                        def _make_jump(p):
                                            return lambda _: self._async_nav("goto", page=p)
                                        self.chapters_menu.add(rumps.MenuItem(lbl, callback=_make_jump(ch_page)))
                                elif tot_p_val > 1:
                                    landmarks = [
                                        ("Start of Document", 1),
                                        ("25%", max(1, int(round(tot_p_val * 0.25)))),
                                        ("50% (Halfway)", max(1, int(round(tot_p_val * 0.50)))),
                                        ("75%", max(1, int(round(tot_p_val * 0.75)))),
                                        ("End of Document", tot_p_val),
                                    ]
                                    seen = set()
                                    self._current_chapters = []
                                    for name, p in landmarks:
                                        if p not in seen:
                                            seen.add(p)
                                            self._current_chapters.append((name, p))
                                            lbl = f"📑 {name} (p. {p})"
                                            def _make_jump_lm(p_val):
                                                return lambda _: self._async_nav("goto", page=p_val)
                                            self.chapters_menu.add(rumps.MenuItem(lbl, callback=_make_jump_lm(p)))
                                else:
                                    self._current_chapters = []
                                    self.chapters_menu.add(rumps.MenuItem("Single page document"))

                            if not is_recent_nav:
                                self.doc_item.title = f"📖 {title_short} ({status.reading_state.current_page}/{status.reading_state.total_pages})"
                                if hasattr(self, "page_slider") and self.page_slider is not None:
                                    self.page_slider.setDoubleValue_(float(cur))
                                if hasattr(self, "slider_page_badge") and self.slider_page_badge is not None:
                                    self.slider_page_badge.setStringValue_(f"p. {cur} / {tot}")
                                if hasattr(self, "slider_chapter_label") and self.slider_chapter_label is not None:
                                    ch_name = self._get_chapter_title_for_page(cur)
                                    self.slider_chapter_label.setStringValue_(f"📑 {ch_name}" if ch_name else "")
                        else:
                            self._current_chapters = []
                            self.doc_item.title = "No active document open"
                            if hasattr(self, "page_slider") and self.page_slider is not None:
                                self.page_slider.setEnabled_(False)
                                if hasattr(self, "slider_page_badge") and self.slider_page_badge is not None:
                                    self.slider_page_badge.setStringValue_("p. - / -")
                                if hasattr(self, "slider_chapter_label") and self.slider_chapter_label is not None:
                                    self.slider_chapter_label.setStringValue_("")
                            if getattr(self, "_last_loaded_toc_sig", None) is not None:
                                self._last_loaded_toc_sig = None
                                self.chapters_menu.clear()
                                self.chapters_menu.add(rumps.MenuItem("No active document"))
                    else:
                        self._current_chapters = []
                        self.title = "📖 (offline)"
                        self.status_item.title = "✗ Disconnected"
                        self.doc_item.title = "Device Offline"
                        if hasattr(self, "slider_chapter_label") and self.slider_chapter_label is not None:
                            self.slider_chapter_label.setStringValue_("")
                        if getattr(self, "_last_loaded_toc_sig", None) is not None:
                            self._last_loaded_toc_sig = None
                            self.chapters_menu.clear()
                            self.chapters_menu.add(rumps.MenuItem("No active document"))

                self._dispatch_to_main(_apply_ui)

            except Exception as e:
                logger.error(f"Error in menubar telemetry refresh: {e}", exc_info=True)
                def _apply_err():
                    self.title = "📖 Quaderno"
                self._dispatch_to_main(_apply_err)
            finally:
                self._telemetry_in_progress = False

        return bg_worker.submit(_fetch_coro())

    def _execute_push_or_summarize(
        self,
        target: str,
        title: Optional[str] = None,
        page: int = 1,
        destination_folder: Optional[str] = None,
    ):
        """Execute document/URL push with folder selection dialog and E-ink optimization."""
        target_dest = destination_folder
        if target_dest is None:
            # Prompt user with native macOS Folder Browser dialog rooted at mirror
            last_used = getattr(self, "_last_dest_folder", None) or "Document"
            resp = prompt_folder_dialog(
                title="Select Destination Folder on Quaderno",
                initial_folder=last_used,
                root_mirror=settings.sync_dir,
            )
            if resp is None:
                # User cancelled dialog
                return None
            target_dest = resp.strip() or "Document"
            self._last_dest_folder = target_dest

        async def _worker_coro():
            try:
                display_name = title or (Path(target).name if Path(target).exists() else target[:40])
                page_suffix = f" (Page {page})" if page > 1 else ""
                notify("Quaderno Companion", "Ingesting...", f"Processing {display_name}{page_suffix}...")
                res = await tool_push_document(
                    source_url_or_path=target,
                    title=title,
                    page=page,
                    destination_folder=target_dest,
                )

                notify("Quaderno Companion", "Pushed to Device", res.get("message", "Sent to device."))
                self.refresh_telemetry()
            except Exception as e:
                logger.error(f"Error executing push: {e}", exc_info=True)
                show_alert("Quaderno Push Error", str(e))

        return bg_worker.submit(_worker_coro())

    def push_browser_tab(self, _):
        """Push or summarize the active web page currently open in Firefox, Safari, or Chrome."""
        from quaderno_companion.triggers.browser import get_active_browser_tab

        tab = get_active_browser_tab()
        if not tab:
            show_alert(
                "No Active Browser Tab Found",
                "Could not detect an active web page in Firefox, Safari, or Chrome.\n\nPlease open a web page in your browser and try again."
            )
            return
        self._execute_push_or_summarize(target=tab["url"], title=tab["title"])

    def push_active_window(self, _):
        """Capture and push the currently active macOS window."""
        from quaderno_companion.triggers.window import capture_active_window_pdf

        try:
            pdf_path, filename, title = capture_active_window_pdf(profile_name=settings.default_profile)
            self._execute_push_or_summarize(target=str(pdf_path), title=title)
        except Exception as e:
            show_alert("Window Capture Error", f"Failed to capture active window: {e}")

    def push_clipboard(self, _):
        """Push or summarize URL/text currently in macOS clipboard."""
        clip_text = self._get_clipboard_text().strip()
        if not clip_text:
            show_alert("Clipboard Empty", "Please copy some text or a URL to your clipboard first.")
            return
        self._execute_push_or_summarize(target=clip_text)

    def push_preview(self, _):
        """Push or summarize the PDF currently open in Apple Preview at active page."""
        path, page_num = get_preview_document_info()
        if not path:
            show_alert(
                "Preview Document Not Found",
                "No active document was detected in Apple Preview. Please open a PDF in Preview first."
            )
            return
        self._execute_push_or_summarize(target=path, title=Path(path).stem, page=page_num)

    def choose_and_push_file(self, _):
        """Open native macOS file dialog to pick and push/summarize a local document."""
        script = 'POSIX path of (choose file of type {"pdf", "epub", "mobi", "md", "txt", "html"} with prompt "Choose Document for Quaderno")'
        try:
            out = subprocess.check_output(["osascript", "-e", script], text=True).strip()
            if out:
                self._execute_push_or_summarize(target=out, title=Path(out).stem)
        except Exception:
            pass  # User cancelled dialog

    def push_url_dialog(self, _):
        """Prompt user for a URL or ArXiv paper link to push."""
        clip = self._get_clipboard_text().strip()
        default_url = clip if clip.startswith("http") else ""
        url = prompt_text_dialog(
            title="Push to Quaderno",
            prompt="Enter URL or ArXiv Paper Link:",
            default_text=default_url,
        )
        if url:
            self._execute_push_or_summarize(target=url)

    def nav_next(self, _):
        """Advance page on Quaderno."""
        self._async_nav("next")

    def nav_prev(self, _):
        """Previous page on Quaderno."""
        self._async_nav("prev")

    def _async_nav(self, action: Literal["next", "prev", "goto", "offset"], page: Optional[int] = None):
        import time
        reading_state = getattr(self, "_last_reading_state", None) or getattr(device_manager, "_reading_state", None)
        if not reading_state or not reading_state.document_id:
            device_manager._load_persisted_state()
            reading_state = device_manager._reading_state

        cur = getattr(reading_state, "current_page", 1) if reading_state else 1
        tot = max(1, getattr(reading_state, "total_pages", 1)) if reading_state else 1
        doc_title = (reading_state.title if reading_state and reading_state.title else None) or "Document"

        if action == "next":
            target_page = min(tot, cur + 1)
        elif action == "prev":
            target_page = max(1, cur - 1)
        elif action == "goto":
            if page is None:
                return
            target_page = max(1, min(tot, page))
        elif action == "offset":
            delta = page or 0
            target_page = max(1, min(tot, cur + delta))
        else:
            return

        if reading_state:
            reading_state.current_page = target_page
            self._last_reading_state = reading_state
        if hasattr(device_manager, "_reading_state"):
            device_manager._reading_state.current_page = target_page
            device_manager._last_nav_time = time.time()
        self._last_user_nav_time = time.time()

        def _apply_optimistic():
            title_short = doc_title[:24] + ("..." if len(doc_title) > 24 else "")
            self.doc_item.title = f"📖 {title_short} ({target_page}/{tot})"
            if hasattr(self, "page_slider") and self.page_slider is not None:
                self.page_slider.setDoubleValue_(float(target_page))
            if hasattr(self, "slider_page_badge") and self.slider_page_badge is not None:
                self.slider_page_badge.setStringValue_(f"p. {target_page} / {tot}")
            if hasattr(self, "slider_chapter_label") and self.slider_chapter_label is not None:
                ch_name = self._get_chapter_title_for_page(target_page)
                self.slider_chapter_label.setStringValue_(f"📑 {ch_name}" if ch_name else "")

        self._dispatch_to_main(_apply_optimistic)

        async def _nav_coro():
            try:
                await tool_navigate_reader(action="goto", page=target_page)
                self.refresh_telemetry()
            except Exception as e:
                show_alert("Navigation Error", str(e))
                self.refresh_telemetry()

        return bg_worker.submit(_nav_coro())

    def _get_clipboard_text(self) -> str:
        """Get current text from system clipboard (pbpaste on macOS, wl-paste / xclip on Linux)."""
        if sys.platform == "darwin":
            try:
                return subprocess.check_output(["pbpaste"], text=True)
            except Exception:
                return ""

        # Linux Wayland
        try:
            return subprocess.check_output(["wl-paste"], text=True, timeout=1.0, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # Linux X11 (xclip)
        try:
            return subprocess.check_output(["xclip", "-selection", "clipboard", "-o"], text=True, timeout=1.0, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        return ""


def start_menubar_app(start_server: bool = True):
    """Launch the background daemon and native macOS menubar application."""
    from quaderno_companion.config import setup_logging
    setup_logging(log_level="INFO", enable_file_logging=True, enable_console_logging=False)
    logger.info("Starting Quaderno Companion Menubar application...")
    if sys.platform != "darwin":
        # On non-macOS platforms, run the server daemon
        from quaderno_companion.server import start_server as _run_server
        _run_server(host=settings.server_host, port=settings.server_port)
        return

    if start_server:
        # Start FastAPI daemon in background thread
        server_thread = threading.Thread(
            target=lambda: uvicorn.run(
                "quaderno_companion.server:app",
                host=settings.server_host,
                port=settings.server_port,
                log_level="warning",
            ),
            daemon=True,
        )
        server_thread.start()

    # Launch native menu bar app
    app = QuadernoMenubarApp()
    app.run()
