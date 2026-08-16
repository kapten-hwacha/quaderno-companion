#!/usr/bin/env python3
"""
Ephemeral NotebookLM Notebook Lifecycle for Quaderno Companion
"""

import argparse
from pathlib import Path
import re
import sys
import time
from typing import Optional
from patchright.sync_api import sync_playwright

from quaderno_companion.notebooklm.auth_manager import AuthManager
from quaderno_companion.notebooklm.browser_utils import BrowserFactory, StealthUtils
from quaderno_companion.notebooklm.config import QUERY_INPUT_SELECTORS, RESPONSE_SELECTORS


def create_and_summarize(
    question: str,
    content_text: Optional[str] = None,
    source_file: Optional[str] = None,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    cleanup: bool = True,
    headless: bool = True,
) -> Optional[str]:
    """
    Create a new ephemeral notebook, add source, ask question, and delete notebook.
    """
    auth = AuthManager()
    if not auth.is_authenticated():
        print("⚠️ Not authenticated. Run: python -m quaderno_companion.notebooklm.auth_manager setup")
        return None

    doc_title = title or "Document Brief"
    print(f"✨ Spawning dedicated ephemeral notebook for '{doc_title}'...")

    playwright = None
    context = None
    created_notebook_id = None

    try:
        playwright = sync_playwright().start()
        context = BrowserFactory.launch_persistent_context(playwright, headless=headless)
        page = context.pages[0] if context.pages else context.new_page()

        print("  🌐 Opening Notebook home...")
        page.goto("https://notebook.google.com/", wait_until="domcontentloaded", timeout=30000)

        # Allow transient SSO / CheckCookie redirects to complete
        start_settle = time.time()
        while time.time() - start_settle < 10:
            if "accounts.google.com" not in page.url:
                break
            # If Google Account Chooser is shown, click the first account if available
            try:
                acc_btn = page.query_selector('li[data-identifier], [data-email], li[data-authuser]')
                if acc_btn and acc_btn.is_visible():
                    print("  👉 Selecting existing Google account...")
                    acc_btn.click()
                    time.sleep(2)
            except Exception:
                pass
            time.sleep(0.5)

        if "accounts.google.com" in page.url and (page.query_selector('input[type="password"]') or page.query_selector('input[type="email"]')):
            print("⚠️ Session expired. Google authentication required.")
            return None

        # If on promo landing page, click Try NotebookLM / Sign In
        for landing_sel in [
            'a:has-text("Try NotebookLM")',
            'button:has-text("Try NotebookLM")',
            'a:has-text("Try Notebook")',
            'button:has-text("Try Notebook")',
            'a:has-text("Get started")',
            'button:has-text("Get started")',
            'a:has-text("Sign in")',
            'button:has-text("Sign in")',
        ]:
            try:
                lbtn = page.query_selector(landing_sel)
                if lbtn and lbtn.is_visible():
                    print(f"  👉 Entering app via '{landing_sel}'...")
                    lbtn.click()
                    time.sleep(2)
                    break
            except Exception:
                pass

        print("  📝 Creating fresh notebook...")
        create_btn = None
        create_selectors = [
            'button:has-text("Create new")',
            'button:has-text("New notebook")',
            'button:has-text("Create")',
            'mat-card.create-new-notebook',
            '[aria-label*="Create new" i]',
            '[aria-label*="Create notebook" i]',
            '[aria-label*="New notebook" i]',
            '.create-new-notebook',
            '.create-notebook-button',
            'button:has-text("Add notebook")',
        ]
        for sel in create_selectors:
            try:
                create_btn = page.wait_for_selector(sel, timeout=4000, state="visible")
                if create_btn:
                    print(f"  👉 Clicked '{sel}'")
                    create_btn.click()
                    break
            except Exception:
                continue

        start_wait = time.time()
        while time.time() - start_wait < 25:
            open_pages = [p for p in context.pages if not p.is_closed()]
            for p in open_pages:
                match = re.search(r"/notebook/([a-zA-Z0-9_-]+)", p.url)
                if match:
                    created_notebook_id = match.group(1)
                    page = p
                    print(f"  ✓ Notebook created: {created_notebook_id}")
                    break
            if created_notebook_id:
                break
            time.sleep(0.5)

        if not created_notebook_id:
            match = re.search(r"/notebook/([a-zA-Z0-9_-]+)", page.url)
            if match:
                created_notebook_id = match.group(1)
                print(f"  ✓ Notebook created: {created_notebook_id}")
            else:
                print(f"  ⚠️ Could not detect notebook ID from URL: {page.url}")

        time.sleep(1.5)

        source_added = False

        if source_file and Path(source_file).exists():
            print(f"  📄 Uploading source file: {source_file}...")
            file_input = page.query_selector('input[type="file"]')
            if file_input:
                file_input.set_input_files(source_file)
                source_added = True
            else:
                try:
                    upload_btn = page.wait_for_selector('button:has-text("Upload")', timeout=3000)
                    if upload_btn:
                        with page.expect_file_chooser() as fc_info:
                            upload_btn.click()
                        file_chooser = fc_info.value
                        file_chooser.set_files(source_file)
                        source_added = True
                except Exception:
                    pass

        elif source_url and source_url.startswith("http"):
            print(f"  🔗 Ingesting source link: {source_url}...")
            try:
                link_btn = page.wait_for_selector('button:has-text("Link"), button:has-text("Website")', timeout=4000)
                if link_btn:
                    link_btn.click()
                    time.sleep(0.5)
                    url_input = page.wait_for_selector('input[type="url"], input[type="text"]', timeout=4000)
                    if url_input:
                        url_input.fill(source_url)
                        insert_btn = page.wait_for_selector('button:has-text("Insert"), button:has-text("Add")', timeout=4000)
                        if insert_btn:
                            insert_btn.click()
                            source_added = True
            except Exception as e:
                print(f"  ⚠️ Link ingestion note: {e}")

        if not source_added and content_text:
            print(f"  📋 Ingesting pasted content text ({len(content_text)} chars)...")
            try:
                text_btn = None
                text_btn_selectors = [
                    'button:has-text("Copied text")',
                    'button:has-text("Paste text")',
                    'button:has-text("Text")',
                    '[data-source-type="text"]',
                ]
                for tsel in text_btn_selectors:
                    try:
                        text_btn = page.wait_for_selector(tsel, timeout=3000, state="visible")
                        if text_btn:
                            text_btn.click()
                            break
                    except Exception:
                        continue

                time.sleep(0.5)
                text_area = page.wait_for_selector('textarea, [contenteditable="true"]', timeout=5000)
                if text_area:
                    text_area.fill(content_text[:20000])
                    for isel in ['button:has-text("Insert")', 'button:has-text("Save")', 'button:has-text("Done")']:
                        try:
                            insert_btn = page.wait_for_selector(isel, timeout=3000)
                            if insert_btn:
                                insert_btn.click()
                                source_added = True
                                break
                        except Exception:
                            continue
            except Exception as e:
                print(f"  ⚠️ Text ingestion note: {e}")

        print("  ⏳ Waiting for source indexing & query input...")
        query_element = None
        for selector in QUERY_INPUT_SELECTORS:
            try:
                query_element = page.wait_for_selector(selector, timeout=25000, state="visible")
                if query_element:
                    break
            except Exception:
                continue

        if not query_element:
            print("  ❌ Query input did not become ready")
            return None

        print("  💬 Querying brief...")
        input_selector = QUERY_INPUT_SELECTORS[0]
        StealthUtils.human_type(page, input_selector, question)
        page.keyboard.press("Enter")

        print("  ⏳ Waiting for synthesized response...")
        answer = None
        stable_count = 0
        last_text = None
        deadline = time.time() + 120

        while time.time() < deadline:
            try:
                thinking = page.query_selector("div.thinking-message")
                if thinking and thinking.is_visible():
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
            print("  ❌ Response timeout")
            return None

        print("  ✅ Received response!")
        return answer

    except Exception as e:
        print(f"  ❌ Error in ephemeral notebook: {e}")
        return None

    finally:
        if cleanup and created_notebook_id and page:
            try:
                print(f"  🧹 Cleaning up ephemeral notebook {created_notebook_id}...")
                menu_btn = None
                for msel in [
                    'button[aria-label*="More options" i]',
                    'button[aria-label*="actions" i]',
                    'button[aria-label*="menu" i]',
                    'mat-icon:has-text("more_vert")',
                ]:
                    try:
                        menu_btn = page.query_selector(msel)
                        if menu_btn and menu_btn.is_visible():
                            menu_btn.click()
                            break
                    except Exception:
                        continue

                time.sleep(0.5)
                del_btn = page.query_selector('button:has-text("Delete notebook"), [role="menuitem"]:has-text("Delete")')
                if del_btn:
                    del_btn.click()
                    time.sleep(0.5)
                    confirm_btn = page.query_selector('button:has-text("Delete")')
                    if confirm_btn:
                        confirm_btn.click()
                        time.sleep(0.5)
                        print("  ✓ Ephemeral notebook deleted.")
            except Exception as e:
                print(f"  ⚠️ Cleanup note: {e}")

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
    parser = argparse.ArgumentParser(description="Spawn ephemeral NotebookLM notebook and summarize")
    parser.add_argument("--question", required=True, help="Question / prompt to submit")
    parser.add_argument("--content", help="Text content to ingest")
    parser.add_argument("--file", help="Source file path to ingest")
    parser.add_argument("--url", help="Source URL to ingest")
    parser.add_argument("--title", help="Document title")
    parser.add_argument("--cleanup", action="store_true", default=True, help="Clean up notebook after query")
    parser.add_argument("--no-cleanup", dest="cleanup", action="store_false", help="Keep notebook")
    parser.add_argument("--show-browser", action="store_true", help="Show browser for debugging")

    args = parser.parse_args()

    res = create_and_summarize(
        question=args.question,
        content_text=args.content,
        source_file=args.file,
        source_url=args.url,
        title=args.title,
        cleanup=args.cleanup,
        headless=not args.show_browser,
    )

    if res:
        print(res)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
