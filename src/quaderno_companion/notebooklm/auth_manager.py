#!/usr/bin/env python3
"""
Authentication Manager for NotebookLM
Handles Google login and browser state persistence
"""

import json
import time
import argparse
import shutil
import re
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from patchright.sync_api import sync_playwright, BrowserContext

from quaderno_companion.notebooklm.config import BROWSER_STATE_DIR, STATE_FILE, AUTH_INFO_FILE, DATA_DIR
from quaderno_companion.notebooklm.browser_utils import BrowserFactory


class AuthManager:
    """
    Manages authentication and browser state for NotebookLM
    """

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)

        self.state_file = STATE_FILE
        self.auth_info_file = AUTH_INFO_FILE
        self.browser_state_dir = BROWSER_STATE_DIR

    def is_authenticated(self) -> bool:
        """Check if valid authentication exists"""
        if not self.state_file.exists():
            return False

        age_days = (time.time() - self.state_file.stat().st_mtime) / 86400
        if age_days > 7:
            print(f"⚠️ Browser state is {age_days:.1f} days old, may need re-authentication")

        return True

    def get_auth_info(self) -> Dict[str, Any]:
        """Get authentication information"""
        info = {
            'authenticated': self.is_authenticated(),
            'state_file': str(self.state_file),
            'state_exists': self.state_file.exists()
        }

        if self.auth_info_file.exists():
            try:
                with open(self.auth_info_file, 'r') as f:
                    saved_info = json.load(f)
                    info.update(saved_info)
            except Exception:
                pass

        if info['state_exists']:
            age_hours = (time.time() - self.state_file.stat().st_mtime) / 3600
            info['state_age_hours'] = age_hours

        return info

    @staticmethod
    def _is_notebooklm_app(url: Optional[str]) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        if "accounts.google.com" in url_lower:
            return False
        return (
            "notebook.google" in url_lower
            or "notebooklm.google" in url_lower
            or "notebook.google.com" in url_lower
            or "notebooklm.google.com" in url_lower
        )

    def setup_auth(self, headless: bool = False, timeout_minutes: int = 10) -> bool:
        """
        Perform interactive authentication setup with robust multi-page detection.
        """
        print("🔐 Starting authentication setup...")
        print(f"  Timeout: {timeout_minutes} minutes")

        playwright = None
        context = None

        try:
            playwright = sync_playwright().start()

            context = BrowserFactory.launch_persistent_context(
                playwright,
                headless=headless
            )

            # Use existing page or create new
            page = context.pages[0] if context.pages else context.new_page()
            print("  🌐 Opening https://notebook.google.com...")
            page.goto("https://notebook.google.com", wait_until="domcontentloaded")

            time.sleep(2)

            # Check if already authenticated
            if self._is_notebooklm_app(page.url):
                print(f"  ✅ Already authenticated! (URL: {page.url})")
                self._save_browser_state(context)
                self._save_auth_info()
                return True

            print("\n  ⏳ Please log in to your Google account in the opened Chrome window...")
            print(f"  ⏱️  Waiting up to {timeout_minutes} minutes for login to complete...")

            deadline = time.time() + (timeout_minutes * 60)
            login_detected = False

            while time.time() < deadline:
                open_pages = [p for p in context.pages if not p.is_closed()]

                for p in open_pages:
                    try:
                        current_url = p.url

                        # Check if any tab is now inside NotebookLM / Notebook
                        if self._is_notebooklm_app(current_url):
                            print(f"\n  ✅ Login detected on {current_url}!")
                            login_detected = True
                            break

                        # If user is on Google landing page, auto-click 'Try NotebookLM' or 'Sign in'
                        if "notebooklm.google" in current_url or "notebook.google" in current_url:
                            for btn_sel in [
                                'a:has-text("Try NotebookLM")',
                                'button:has-text("Try NotebookLM")',
                                'a:has-text("Try Notebook")',
                                'button:has-text("Try Notebook")',
                                'a:has-text("Sign in")',
                                'button:has-text("Sign in")',
                                'a:has-text("Get started")',
                                'button:has-text("Get started")',
                                '[aria-label*="Try Notebook" i]',
                            ]:
                                try:
                                    btn = p.query_selector(btn_sel)
                                    if btn and btn.is_visible():
                                        print(f"  👉 Clicking '{btn_sel}' to enter Notebook...")
                                        btn.click()
                                        time.sleep(1.5)
                                        break
                                except Exception:
                                    pass

                        # If login completed and Google sent user to myaccount.google.com or google.com, redirect to Notebook
                        if "accounts.google.com" not in current_url and ("myaccount.google.com" in current_url or "google.com/search" in current_url):
                            print("  🔄 Google account authenticated, redirecting to Notebook...")
                            p.goto("https://notebook.google.com", wait_until="domcontentloaded")
                            time.sleep(2)
                    except Exception:
                        pass

                if login_detected:
                    break

                # Also inspect cookies to detect Google auth session
                try:
                    cookies = context.cookies()
                    has_auth_cookies = any(c.get("name") in ("SID", "SSID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID") for c in cookies)
                    if has_auth_cookies:
                        # Check if any page is still on accounts.google.com
                        accounts_pages = [p for p in open_pages if "accounts.google.com" in p.url]
                        if not accounts_pages:
                            # Try navigating to Notebook to finalize session
                            target_page = open_pages[0] if open_pages else page
                            if not self._is_notebooklm_app(target_page.url):
                                print("  🔄 Auth cookies detected, navigating to Notebook...")
                                target_page.goto("https://notebook.google.com", wait_until="domcontentloaded")
                                time.sleep(2)
                                if self._is_notebooklm_app(target_page.url):
                                    login_detected = True
                                    break
                except Exception:
                    pass

                time.sleep(1.5)

            if not login_detected:
                print(f"\n  ❌ Authentication timeout: {timeout_minutes} minutes elapsed without login detection.")
                return False

            # Wait for cookies to settle and save state
            time.sleep(2)
            self._save_browser_state(context)
            self._save_auth_info()
            print("  💾 Saved authentication state successfully.")
            return True

        except Exception as e:
            print(f"  ❌ Error during authentication setup: {e}")
            return False

        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def _save_browser_state(self, context: BrowserContext):
        """Save browser state to disk"""
        try:
            context.storage_state(path=str(self.state_file))
            print(f"  💾 Saved browser state to: {self.state_file}")
        except Exception as e:
            print(f"  ❌ Failed to save browser state: {e}")
            raise

    def _save_auth_info(self):
        """Save authentication metadata"""
        try:
            info = {
                'authenticated_at': time.time(),
                'authenticated_at_iso': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self.auth_info_file, 'w') as f:
                json.dump(info, f, indent=2)
        except Exception:
            pass

    def clear_auth(self) -> bool:
        """Clear all authentication data"""
        print("🗑️ Clearing authentication data...")

        try:
            if self.state_file.exists():
                self.state_file.unlink()
                print("  ✅ Removed browser state")

            if self.auth_info_file.exists():
                self.auth_info_file.unlink()
                print("  ✅ Removed auth info")

            if self.browser_state_dir.exists():
                shutil.rmtree(self.browser_state_dir)
                self.browser_state_dir.mkdir(parents=True, exist_ok=True)
                print("  ✅ Cleared browser data")

            return True

        except Exception as e:
            print(f"  ❌ Error clearing auth: {e}")
            return False

    def re_auth(self, headless: bool = False, timeout_minutes: int = 10) -> bool:
        """Perform re-authentication (clear and setup)"""
        self.clear_auth()
        return self.setup_auth(headless, timeout_minutes)


def main():
    parser = argparse.ArgumentParser(description='Manage NotebookLM authentication')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    setup_parser = subparsers.add_parser('setup', help='Setup authentication')
    setup_parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    setup_parser.add_argument('--timeout', type=float, default=10, help='Login timeout in minutes (default: 10)')

    subparsers.add_parser('status', help='Check authentication status')
    subparsers.add_parser('clear', help='Clear authentication')

    reauth_parser = subparsers.add_parser('reauth', help='Re-authenticate (clear + setup)')
    reauth_parser.add_argument('--timeout', type=float, default=10, help='Login timeout in minutes (default: 10)')

    args = parser.parse_args()
    auth = AuthManager()

    if args.command == 'setup':
        if auth.setup_auth(headless=args.headless, timeout_minutes=args.timeout):
            print("\n✅ Authentication setup complete!")
        else:
            print("\n❌ Authentication setup failed")
            sys.exit(1)
    elif args.command == 'status':
        info = auth.get_auth_info()
        print("\n🔐 Authentication Status:")
        print(f"  Authenticated: {'Yes' if info['authenticated'] else 'No'}")
        print(f"  State file: {info['state_file']}")
    elif args.command == 'clear':
        auth.clear_auth()
    elif args.command == 'reauth':
        auth.re_auth(timeout_minutes=args.timeout)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
