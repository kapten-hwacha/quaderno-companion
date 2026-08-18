#!/usr/bin/env python3
"""Build and package browser extensions for Quaderno Companion."""

import json
import shutil
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRIGGERS_DIR = PROJECT_ROOT / "triggers"
DIST_DIR = PROJECT_ROOT / "dist"


def get_version() -> str:
    """Read version from pyproject.toml."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data.get("project", {}).get("version", "0.1.0")


def zip_directory(source_dir: Path, output_zip: Path):
    """Zip the contents of a directory into an archive."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                arcname = file_path.relative_to(source_dir)
                zipf.write(file_path, arcname)
    print(f"Created extension bundle: {output_zip} ({output_zip.stat().st_size / 1024:.1f} KB)")


def build_extensions():
    """Package Chrome and Firefox extensions."""
    version = get_version()
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    chrome_dir = TRIGGERS_DIR / "chrome-extension"
    if chrome_dir.exists():
        chrome_zip = DIST_DIR / f"quaderno-chrome-extension-v{version}.zip"
        zip_directory(chrome_dir, chrome_zip)

    firefox_dir = TRIGGERS_DIR / "firefox-extension"
    if firefox_dir.exists():
        firefox_zip = DIST_DIR / f"quaderno-firefox-extension-v{version}.zip"
        zip_directory(firefox_dir, firefox_zip)


if __name__ == "__main__":
    build_extensions()
