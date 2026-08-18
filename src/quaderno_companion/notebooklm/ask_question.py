#!/usr/bin/env python3
"""
Simple NotebookLM Question Interface
"""

import argparse
import re
import sys
import time
from patchright.sync_api import sync_playwright

from quaderno_companion.notebooklm.auth_manager import AuthManager
from quaderno_companion.notebooklm.notebook_manager import NotebookLibrary
from quaderno_companion.notebooklm.config import QUERY_INPUT_SELECTORS, RESPONSE_SELECTORS
from quaderno_companion.notebooklm.browser_utils import BrowserFactory, StealthUtils

FOLLOW_UP_REMINDER = (
    "\n\nEXTREMELY IMPORTANT: Is that ALL you need to know? "
    "You can always ask another question! Think about it carefully: "
    "before you reply to the user, review their original request and this answer. "
    "If anything is still unclear or missing, ask me another comprehensive question "
    "that includes all necessary context (since each question opens a new browser session)."
)


def ask_notebooklm(question: str, notebook_url: str, headless: bool = True) -> str | None:
    """
    Ask a question to NotebookLM
    """
    auth = AuthManager()

    if not auth.is_authenticated():
        print("⚠️ Not authenticated. Run: python -m quaderno_companion.notebooklm.auth_manager setup")
        return None

    print(f"💬 Asking: {question}")
    print(f"📚 Notebook: {notebook_url}")

    playwright = None
    context = None

    try:
        playwright = sync_playwright().start()
        context = BrowserFactory.launch_persistent_context(
            playwright,
            headless=headless
        )

        page = context.pages[0] if context.pages else context.new_page()
        print("  🌐 Opening notebook...")
        page.goto(notebook_url, wait_until="domcontentloaded")
        page.wait_for_url(re.compile(r"^https://(?:notebook|notebooklm)\.google(?:\.com)?/"), timeout=10000)

        print("  ⏳ Waiting for query input...")
        query_element = None
        for selector in QUERY_INPUT_SELECTORS:
            try:
                query_element = page.wait_for_selector(
                    selector,
                    timeout=10000,
                    state="visible"
                )
                if query_element:
                    break
            except Exception:
                continue

        if not query_element:
            print("  ❌ Could not find query input")
            return None

        print("  ⏳ Typing question...")
        input_selector = QUERY_INPUT_SELECTORS[0]
        StealthUtils.human_type(page, input_selector, question)

        print("  📤 Submitting...")
        page.keyboard.press("Enter")
        StealthUtils.random_delay(500, 1500)

        print("  ⏳ Waiting for answer...")
        answer = None
        stable_count = 0
        last_text = None
        deadline = time.time() + 120

        while time.time() < deadline:
            try:
                thinking_element = page.query_selector('div.thinking-message')
                if thinking_element and thinking_element.is_visible():
                    time.sleep(1)
                    continue
            except Exception:
                pass

            for selector in RESPONSE_SELECTORS:
                try:
                    elements = page.query_selector_all(selector)
                    if elements:
                        latest = elements[-1]
                        text = latest.inner_text().strip()
                        if text:
                            if text == last_text:
                                stable_count += 1
                                if stable_count >= 3:
                                    answer = text
                                    break
                            else:
                                stable_count = 0
                                last_text = text
                except Exception:
                    continue

            if answer:
                break
            time.sleep(1)

        if not answer:
            print("  ❌ Timeout waiting for answer")
            return None

        print("  ✅ Got answer!")
        return answer + FOLLOW_UP_REMINDER

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None

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


def main():
    parser = argparse.ArgumentParser(description='Ask NotebookLM a question')
    parser.add_argument('--question', required=True, help='Question to ask')
    parser.add_argument('--notebook-url', help='NotebookLM notebook URL')
    parser.add_argument('--notebook-id', help='Notebook ID from library')
    parser.add_argument('--show-browser', action='store_true', help='Show browser')

    args = parser.parse_args()
    notebook_url = args.notebook_url

    if not notebook_url and args.notebook_id:
        library = NotebookLibrary()
        notebook = library.get_notebook(args.notebook_id)
        if notebook:
            notebook_url = notebook['url']

    if not notebook_url:
        library = NotebookLibrary()
        active = library.get_active_notebook()
        if active:
            notebook_url = active['url']

    if not notebook_url:
        print("❌ Please provide --notebook-url or --notebook-id")
        sys.exit(1)

    answer = ask_notebooklm(
        question=args.question,
        notebook_url=notebook_url,
        headless=not args.show_browser
    )

    if answer:
        print(answer)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
