#!/usr/bin/env python3
"""Application Launcher for Quaderno Companion Standalone macOS Bundle."""

import os
import sys
import logging

# Ensure objective-c / pyobjc fork safety on macOS
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("quaderno-companion-launcher")


def main():
    """Bootstrap and run Quaderno Companion menu bar app + background daemon."""
    logger.info("Starting Quaderno Companion standalone app...")
    try:
        from quaderno_companion.triggers.menubar import start_menubar_app
        start_menubar_app(start_server=True)
    except Exception as e:
        logger.exception(f"Fatal error running Quaderno Companion: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
