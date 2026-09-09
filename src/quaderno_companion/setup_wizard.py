"""Setup Wizard utilities for Quaderno Companion."""

import os
from pathlib import Path
from typing import Dict


def update_env_file(filepath: Path, updates: Dict[str, str]) -> None:
    """Safely update or insert key-value pairs in an .env file preserving existing entries."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if filepath.exists():
        lines = filepath.read_text(encoding="utf-8").splitlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    content = "\n".join(new_lines).strip() + "\n"
    filepath.write_text(content, encoding="utf-8")
    try:
        os.chmod(filepath, 0o600)
    except Exception:
        pass

