"""
Browser Utilities for NotebookLM
Handles browser launching, stealth features, and common interactions
"""

import json
from pathlib import Path
import random
import time
from typing import Optional, List

from patchright.sync_api import Playwright, BrowserContext, Page
from quaderno_companion.notebooklm.config import BROWSER_PROFILE_DIR, STATE_FILE, BROWSER_ARGS, USER_AGENT


class BrowserFactory:
    """Factory for creating configured browser contexts"""

    @staticmethod
    def launch_persistent_context(
        playwright: Playwright,
        headless: bool = True,
        user_data_dir: str = str(BROWSER_PROFILE_DIR)
    ) -> BrowserContext:
        """
        Launch a persistent browser context with anti-detection features,
        cookie injection, and automatic Singleton lock recovery.
        """
        profile_path = Path(user_data_dir)
        profile_path.mkdir(parents=True, exist_ok=True)

        # 1. Clean up stale Singleton locks left by terminated Chrome processes
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]:
            lock_file = profile_path / lock_name
            try:
                if lock_file.is_symlink() or lock_file.exists():
                    lock_file.unlink(missing_ok=True)
            except Exception:
                pass

        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                channel="chrome",
                headless=headless,
                no_viewport=True,
                ignore_default_args=["--enable-automation"],
                user_agent=USER_AGENT,
                args=BROWSER_ARGS
            )
            BrowserFactory._inject_cookies(context)
            return context
        except Exception as e:
            if "ProcessSingleton" in str(e) or "SingletonLock" in str(e) or "already in use" in str(e):
                import tempfile
                temp_dir = tempfile.mkdtemp(prefix="quaderno_nb_profile_")
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=temp_dir,
                    channel="chrome",
                    headless=headless,
                    no_viewport=True,
                    ignore_default_args=["--enable-automation"],
                    user_agent=USER_AGENT,
                    args=BROWSER_ARGS
                )
                BrowserFactory._inject_cookies(context)
                return context
            raise

    @staticmethod
    def _inject_cookies(context: BrowserContext):
        """Inject cookies from state.json if available"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    if 'cookies' in state and len(state['cookies']) > 0:
                        context.add_cookies(state['cookies'])
            except Exception as e:
                print(f"  ⚠️  Could not load state.json: {e}")


class StealthUtils:
    """Human-like interaction utilities"""

    @staticmethod
    def random_delay(min_ms: int = 100, max_ms: int = 500):
        """Add random delay"""
        time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    @staticmethod
    def human_type(page: Page, selector: str, text: str, wpm_min: int = 320, wpm_max: int = 480):
        """Type with human-like speed"""
        element = page.query_selector(selector)
        if not element:
            try:
                element = page.wait_for_selector(selector, timeout=2000)
            except Exception:
                pass
        
        if not element:
            print(f"⚠️ Element not found for typing: {selector}")
            return

        element.click()
        
        for char in text:
            element.type(char, delay=random.uniform(25, 75))
            if random.random() < 0.05:
                time.sleep(random.uniform(0.15, 0.4))

    @staticmethod
    def realistic_click(page: Page, selector: str):
        """Click with realistic movement"""
        element = page.query_selector(selector)
        if not element:
            return

        box = element.bounding_box()
        if box:
            page.mouse.move(
                box['x'] + box['width'] / 2,
                box['y'] + box['height'] / 2,
                steps=5
            )
            StealthUtils.random_delay(50, 150)
            page.mouse.click(
                box['x'] + box['width'] / 2,
                box['y'] + box['height'] / 2
            )
        else:
            element.click()
