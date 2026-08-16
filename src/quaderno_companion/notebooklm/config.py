"""
Configuration for NotebookLM Module
Centralizes constants, selectors, and persistent paths
"""

from pathlib import Path

# Paths
MODULE_DIR = Path(__file__).parent
DATA_DIR = Path.home() / ".config" / "quaderno" / "notebooklm" / "data"

# Auto-inherit from existing skill data directory if present
GEMINI_SKILL_DATA = Path.home() / ".gemini" / "config" / "skills" / "notebooklm" / "data"
CLAUDE_SKILL_DATA = Path.home() / ".claude" / "skills" / "notebooklm" / "data"

if not (DATA_DIR / "browser_state" / "state.json").exists():
    if (GEMINI_SKILL_DATA / "browser_state" / "state.json").exists():
        DATA_DIR = GEMINI_SKILL_DATA
    elif (CLAUDE_SKILL_DATA / "browser_state" / "state.json").exists():
        DATA_DIR = CLAUDE_SKILL_DATA

BROWSER_STATE_DIR = DATA_DIR / "browser_state"
BROWSER_PROFILE_DIR = BROWSER_STATE_DIR / "browser_profile"
STATE_FILE = BROWSER_STATE_DIR / "state.json"
AUTH_INFO_FILE = DATA_DIR / "auth_info.json"
LIBRARY_FILE = DATA_DIR / "library.json"

# NotebookLM Selectors
QUERY_INPUT_SELECTORS = [
    "textarea.query-box-input",  # Primary
    'textarea[aria-label="Feld für Anfragen"]',  # Fallback German
    'textarea[aria-label="Input for queries"]',  # Fallback English
]

RESPONSE_SELECTORS = [
    ".to-user-container .message-text-content",  # Primary
    "[data-message-author='bot']",
    "[data-message-author='assistant']",
]

# Browser Configuration
BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',  # Patches navigator.webdriver
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--no-first-run',
    '--no-default-browser-check'
]

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# Timeouts
LOGIN_TIMEOUT_MINUTES = 10
QUERY_TIMEOUT_SECONDS = 120
PAGE_LOAD_TIMEOUT = 30000
