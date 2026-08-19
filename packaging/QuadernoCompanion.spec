# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for Quaderno Companion macOS application."""

import sys
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path.cwd()
SRC_DIR = PROJECT_ROOT / "src"

added_datas = [
    (str(SRC_DIR / "quaderno_companion"), "quaderno_companion"),
]

hidden_imports = [
    # Uvicorn & FastAPI internals
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "fastapi",
    "starlette",
    "starlette.routing",
    "starlette.middleware",
    "pydantic",
    "pydantic_settings",
    "python_multipart",
    # PDF, Images, Compression & Low-level
    "fitz",
    "PIL",
    "PIL.Image",
    "reportlab",
    "reportlab.platypus",
    "reportlab.lib",
    "lz4",
    "lz4.frame",
    "dptrp1",
    "dptrp1.dptrp1",
    # Automation & Network
    "notebooklm",
    "bs4",
    "readability",
    "httpx",
    "typer",
    "rich",
    "jinja2",
]

if sys.platform == "darwin":
    hidden_imports.extend([
        "rumps",
        "AppKit",
        "Quartz",
        "Foundation",
        "objc",
        "PyObjCTools",
    ])

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "app_launcher.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="quaderno-companion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="quaderno-companion",
)

if sys.platform == "darwin":
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    _version = "0.1.2"
    try:
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as _f:
            _version = tomllib.load(_f).get("project", {}).get("version", "0.1.2")
    except Exception:
        pass

    app = BUNDLE(
        coll,
        name="Quaderno Companion.app",
        icon=str(PROJECT_ROOT / "packaging" / "assets" / "QuadernoCompanion.icns"),
        bundle_identifier="com.quaderno.companion",
        info_plist={
            "CFBundleName": "Quaderno Companion",
            "CFBundleDisplayName": "Quaderno Companion",
            "CFBundleIdentifier": "com.quaderno.companion",
            "CFBundleVersion": _version,
            "CFBundleShortVersionString": _version,
            "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "quaderno-companion",
            "CFBundleIconFile": "QuadernoCompanion.icns",
            "LSUIElement": True,  # Menu Bar background app (no Dock icon)
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSSupportsAutomaticGraphicsSwitching": True,
            "NSHumanReadableCopyright": "Copyright © 2026 Karl-Andres Parts. All rights reserved.",
        },
    )
