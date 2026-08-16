"""Setup Wizard for Quaderno Companion (API Keys, Device Pairing, and Preferences)."""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
import typer

from quaderno_companion.config import settings

console = Console()


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


def verify_gemini_api_key(api_key: str, model: str = "gemini-3.5-flash-lite") -> Tuple[bool, str]:
    """Test a Gemini API key against Google Generative Language API."""
    if not api_key or len(api_key.strip()) < 10:
        return False, "API key is too short or empty."

    clean_key = api_key.strip().replace("\x00", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": clean_key}
    payload = {
        "contents": [{"parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 3},
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.is_success:
                return True, "API Key is valid and active!"
            elif resp.status_code == 400:
                return False, "Invalid API key (Google returned HTTP 400 Bad Request)."
            elif resp.status_code == 403:
                return False, "Access Forbidden (HTTP 403). Check project billing or API restrictions."
            elif resp.status_code == 429:
                return False, "Rate limit exceeded (HTTP 429)."
            else:
                return False, f"Google API error (HTTP {resp.status_code}): {resp.text[:120]}"
    except Exception as e:
        return False, f"Connection failed: {e}"


def run_api_setup_wizard(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    target_env: Optional[Path] = None,
    global_env: Optional[Path] = None,
    verify: bool = True,
) -> bool:
    """Interactive wizard to configure and verify Google Gemini API key."""
    console.print(
        Panel(
            "[bold white]Google Gemini API Key Setup Wizard[/bold white]\n\n"
            "This key enables autonomous, ultra-fast E-ink summarization and synthesis\n"
            "for your Fujitsu Quaderno.\n\n"
            "🔗 [cyan]Get a free API key at:[/cyan] [bold underline]https://aistudio.google.com/app/apikey[/bold underline]",
            title="Quaderno Companion AI Setup",
            border_style="cyan",
        )
    )

    # 1. Determine target env file path
    env_file = target_env or Path.cwd() / ".env"
    global_env_file = global_env or (Path.home() / ".config" / "quaderno" / ".env")

    current_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if current_key:
        masked = current_key[:6] + "..." + current_key[-4:] if len(current_key) > 10 else "***"
        console.print(f"Current configured key: [green]{masked}[/green]\n")

    # 2. Prompt for API key if not supplied
    input_key = api_key
    while not input_key:
        prompt_text = "Enter your Google Gemini API Key"
        if current_key:
            prompt_text += " (press Enter to keep current)"
        val = typer.prompt(prompt_text, default="", hide_input=True)
        if not val and current_key:
            input_key = current_key
            break
        if val.strip():
            input_key = val.strip()
            break
        console.print("[yellow]Please enter a valid API key or Ctrl+C to cancel.[/yellow]")

    # 3. Model selection
    chosen_model = model or settings.llm_model or "gemini-3.5-flash-lite"
    console.print(f"\nTarget model: [bold cyan]{chosen_model}[/bold cyan]")

    # 4. Verification
    if verify:
        with console.status(f"[bold cyan]Verifying API key with Google AI ({chosen_model})..."):
            ok, msg = verify_gemini_api_key(input_key, model=chosen_model)

        if ok:
            console.print(f"[bold green]✓ {msg}[/bold green]\n")
        else:
            console.print(f"[bold red]✗ Verification failed:[/bold red] {msg}")
            retry = typer.confirm("Would you like to save this key anyway?", default=False)
            if not retry:
                console.print("[yellow]Setup cancelled. Run `quadctl setup-api` when ready.[/yellow]")
                return False

    # 5. Save to environment file
    updates = {
        "GEMINI_API_KEY": input_key,
        "QUADERNO_LLM_MODEL": chosen_model,
        "QUADERNO_SUMMARIZER_PROVIDER": "gemini_api",
    }

    # Save to local .env
    update_env_file(env_file, updates)
    # Also save to user config .env for background launchd / menubar daemon
    update_env_file(global_env_file, updates)

    console.print(
        Panel(
            f"[bold green]✓ Gemini API successfully configured![/bold green]\n\n"
            f"Saved to: [cyan]{env_file}[/cyan]\n"
            f"Global fallback: [cyan]{global_env_file}[/cyan]\n"
            f"Default model: [bold white]{chosen_model}[/bold white]\n\n"
            f"Try summarizing a link right now:\n"
            f"  [cyan]uv run quadctl summarize https://example.com/article --pages 2[/cyan]",
            title="Setup Complete",
            border_style="green",
        )
    )
    return True
