#!/usr/bin/env python3
"""Unified Release Script for Quaderno Companion.

Builds:
1. Python wheel (.whl) & source distribution (.tar.gz)
2. macOS Standalone Application (.app) & Disk Image (.dmg)
3. SHA256 Checksums file (checksums.sha256)
"""

import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def run_command(cmd, desc):
    """Run a shell command and print status."""
    print(f"\n---> {desc}...")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def generate_checksums():
    """Generate SHA256 checksums for all release artifacts in dist/."""
    checksums_file = DIST_DIR / "checksums.sha256"
    lines = []

    print("\n---> Generating SHA256 Checksums...")
    for artifact in sorted(DIST_DIR.iterdir()):
        if artifact.is_file() and artifact.name != "checksums.sha256" and not artifact.name.startswith("."):
            h = hashlib.sha256()
            with open(artifact, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            digest = h.hexdigest()
            size_mb = artifact.stat().st_size / (1024 * 1024)
            print(f"  {artifact.name:45} [{size_mb:6.2f} MB] {digest}")
            lines.append(f"{digest}  {artifact.name}\n")

    with open(checksums_file, "w") as f:
        f.writelines(lines)
    print(f"Wrote checksums to: {checksums_file}")


def main():
    print("=" * 60)
    print("  QUADERNO COMPANION RELEASE PIPELINE")
    print("=" * 60)

    # 1. Build Python Wheels & Sdist
    run_command(["uv", "build"], "Building Python Wheel and Source Distribution")

    # 2. Build macOS App & DMG (if on macOS)
    if sys.platform == "darwin":
        run_command([sys.executable, str(SCRIPTS_DIR / "build_app.py")], "Building macOS .app and .dmg")
    else:
        print("\nSkipping macOS .app & .dmg build (running on non-macOS system)")

    # 4. Generate Checksums
    generate_checksums()

    print("\n" + "=" * 60)
    print("  RELEASE BUILD SUCCEEDED!")
    print(f"  All artifacts located in: {DIST_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
