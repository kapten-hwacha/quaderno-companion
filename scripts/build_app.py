#!/usr/bin/env python3
"""Build standalone macOS .app bundle and .dmg for Quaderno Companion."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
PACKAGING_DIR = PROJECT_ROOT / "packaging"


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


def ensure_icons():
    """Ensure icon assets are generated."""
    icns_path = PACKAGING_DIR / "assets" / "QuadernoCompanion.icns"
    if not icns_path.exists():
        print("Generating icon assets...")
        generate_script = PACKAGING_DIR / "generate_icons.py"
        subprocess.run([sys.executable, str(generate_script)], check=True)


def run_pyinstaller():
    """Run PyInstaller with the spec file."""
    spec_path = PACKAGING_DIR / "QuadernoCompanion.spec"
    cmd = [
        "pyinstaller",
        str(spec_path),
        "--clean",
        "-y",
    ]
    print(f"Running PyInstaller: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def build_dmg(version: str):
    """Package the .app bundle into a macOS DMG with Applications shortcut."""
    if sys.platform != "darwin":
        print("Skipping DMG creation (not on macOS).")
        return

    app_path = DIST_DIR / "Quaderno Companion.app"
    if not app_path.exists():
        raise FileNotFoundError(f"App bundle not found at {app_path}")

    dmg_path = DIST_DIR / f"Quaderno-Companion-v{version}.dmg"
    staging_dir = DIST_DIR / "dmg_staging"

    print(f"Creating DMG image: {dmg_path.name}...")
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    # Copy .app to staging
    dest_app = staging_dir / "Quaderno Companion.app"
    shutil.copytree(app_path, dest_app, symlinks=True)

    # Create /Applications symlink
    apps_link = staging_dir / "Applications"
    os.symlink("/Applications", apps_link)

    # Create DMG via hdiutil
    if dmg_path.exists():
        dmg_path.unlink()

    cmd = [
        "hdiutil",
        "create",
        "-volname", "Quaderno Companion",
        "-srcfolder", str(staging_dir),
        "-ov",
        "-format", "UDZO",
        str(dmg_path),
    ]
    subprocess.run(cmd, check=True)

    # Clean up staging
    shutil.rmtree(staging_dir)
    print(f"Successfully created DMG: {dmg_path} ({dmg_path.stat().st_size / (1024 * 1024):.1f} MB)")


def main():
    version = get_version()
    print(f"=== Building Quaderno Companion v{version} ===")
    ensure_icons()
    run_pyinstaller()
    build_dmg(version)
    print(f"=== Build Completed! Artifacts in {DIST_DIR} ===")


if __name__ == "__main__":
    main()
