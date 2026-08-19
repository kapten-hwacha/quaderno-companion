"""Command Line Interface (`quadctl`) for Quaderno Companion."""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from quaderno_companion.agent.core import agent
from quaderno_companion.agent.tools import (
    tool_get_reading_state,
    tool_navigate_reader,
    tool_push_document,
)
from quaderno_companion.config import settings
from quaderno_companion.device.manager import device_manager

app = typer.Typer(
    name="quadctl",
    help="Autonomous background companion and desktop bridge for Fujitsu Quaderno Gen 2",
    add_completion=False,
)
console = Console()


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _resolve_prev_doc_deletion(prev_doc: Optional[dict], clean: bool, keep: bool) -> bool:
    """Prompt the user whether to delete the previously pushed document.

    Returns True if the caller should delete the document after a successful push.
    Respects --clean / --keep flags; falls back to an interactive prompt.
    """
    if not (prev_doc and prev_doc.get("doc_id")):
        return False
    if clean:
        return True
    if keep:
        return False
    try:
        ans = typer.prompt(
            f"Delete previously pushed document '{prev_doc.get('title')}' from Quaderno? [y/N]",
            default="N",
        )
        return ans.strip().lower() in ("y", "yes")
    except Exception:
        return False


async def _delete_prev_doc(prev_doc: Optional[dict]) -> None:
    """Delete the previously pushed document from Quaderno, logging outcomes."""
    if not (prev_doc and prev_doc.get("doc_id")):
        return
    try:
        client = await device_manager.get_client()
        client.delete_document(str(prev_doc["doc_id"]))
        rprint(f"[dim]Deleted previous document '{prev_doc.get('title')}' from Quaderno.[/dim]")
    except Exception as e:
        rprint(f"[yellow]Could not delete previous document: {e}[/yellow]")


@app.command()
def serve(
    host: str = typer.Option(settings.server_host, "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(settings.server_port, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable live auto-reloading"),
):
    """Start the Quaderno Companion background daemon and REST server."""
    rprint(
        Panel(
            f"[bold green]Starting Quaderno Companion Daemon[/bold green]\n"
            f"Endpoint: [bold cyan]http://{host}:{port}[/bold cyan]",
            title="Fujitsu Quaderno Bridge",
            border_style="green",
        )
    )
    uvicorn.run("quaderno_companion.server:app", host=host, port=port, reload=reload)


@app.command()
def pair(
    pin: Optional[str] = typer.Option(None, "--pin", "-P", help="PIN shown on Quaderno screen (optional, prompts if omitted)"),
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Override device IP/host for pairing"),
):
    """Pair computer with Quaderno device using a one-time PIN."""
    async def _pair():
        try:
            rprint("[bold yellow]Connecting to Quaderno to initiate pairing...[/bold yellow]")
            client_id, key = await device_manager.pair_device(pin=pin, host=host)
            rprint(
                Panel(
                    f"[bold green]Successfully paired with Quaderno![/bold green]\n"
                    f"Client ID: {client_id}\n"
                    f"Credentials saved to: [cyan]{settings.config_dir}[/cyan]",
                    title="Pairing Complete",
                    border_style="green",
                )
            )
        except Exception as e:
            err_msg = str(e)
            if "'a'" in err_msg or "403" in err_msg:
                rprint("[bold red]Pairing rejected:[/bold red] Incorrect PIN entered or pairing timed out.")
                rprint("[yellow]Tip: When you run `quadctl pair`, look at the PIN that appears on your Quaderno display and enter it.[/yellow]")
            else:
                rprint(f"[bold red]Pairing failed:[/bold red] {e}")
            sys.exit(1)

    run_async(_pair())


@app.command(name="setup-api")
def setup_api_cmd(
    key: Optional[str] = typer.Option(None, "--key", "-k", help="Gemini API Key (skips prompt if provided)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Gemini Model (defaults to gemini-2.5-flash)"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Verify API key with Google AI before saving"),
):
    """Run interactive setup wizard for Google Gemini API key and model selection."""
    from quaderno_companion.setup_wizard import run_api_setup_wizard
    success = run_api_setup_wizard(api_key=key, model=model, verify=verify)
    if not success:
        sys.exit(1)


@app.command(name="setup")
def setup_cmd():
    """Run full interactive setup wizard (Pairing + Gemini AI API Key + Service setup)."""
    rprint(
        Panel(
            "[bold white]Welcome to Fujitsu Quaderno Companion Setup[/bold white]\n\n"
            "This wizard will help you configure:\n"
            "  1. Device pairing and network bridge\n"
            "  2. Google Gemini AI synthesis API key\n"
            "  3. Menu bar app & background LaunchAgent",
            title="Quaderno Companion Setup",
            border_style="magenta",
        )
    )

    # Step 1: Pairing check
    if not device_manager.is_paired:
        rprint("\n[bold yellow]Step 1: Quaderno Device Pairing[/bold yellow]")
        do_pair = typer.confirm("Would you like to pair your Quaderno device now?", default=True)
        if do_pair:
            pin = typer.prompt("Enter PIN shown on Quaderno screen (or press Enter to probe)", default="")
            try:
                run_async(device_manager.pair_device(pin=pin if pin else None))
                rprint("[bold green]✓ Device paired successfully![/bold green]")
            except Exception as e:
                rprint(f"[yellow]Pairing skipped or failed: {e}[/yellow]")
    else:
        rprint("[bold green]✓ Step 1: Device is already paired.[/bold green]")

    # Step 2: Gemini API Key
    rprint("\n[bold yellow]Step 2: Google Gemini AI Configuration[/bold yellow]")
    from quaderno_companion.setup_wizard import run_api_setup_wizard
    run_api_setup_wizard()

    # Step 3: Service installation
    rprint("\n[bold yellow]Step 3: Background Service & Daemon[/bold yellow]")
    if sys.platform == "darwin":
        install_bg = typer.confirm("Would you like to install the background menu bar LaunchAgent?", default=True)
        if install_bg:
            install_service()
    elif sys.platform.startswith("linux"):
        install_bg = typer.confirm("Would you like to install the background systemd user service?", default=True)
        if install_bg:
            install_service()

    rprint("\n[bold green]✓ Setup complete! You're ready to use Quaderno Companion.[/bold green]\n")



@app.command()
def push(
    source: Optional[str] = typer.Argument(None, help="URL, ArXiv paper, or file path (auto-detects active browser tab if omitted)"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Custom document title"),
    page: int = typer.Option(1, "--page", "-p", help="Initial page number"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Target screen ('A4' or 'A5')"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Automatically delete previously pushed document without asking"),
    keep: bool = typer.Option(False, "--keep", "-k", help="Keep previously pushed document without asking"),
):
    """Push a web page, paper, or document to Quaderno with E-ink optimization."""
    from quaderno_companion.triggers.browser import get_active_browser_tab
    from quaderno_companion.state import get_last_pushed_document

    target_source = source
    target_title = title

    if not target_source:
        active_tab = get_active_browser_tab()
        if active_tab:
            target_source = active_tab["url"]
            if not target_title:
                target_title = active_tab["title"]
            rprint(f"[bold cyan]Detected active {active_tab['browser']} tab:[/bold cyan] [white]{active_tab['title']}[/white] ({active_tab['url']})")
        else:
            rprint("[bold red]No active browser tab found in Firefox/Safari/Chrome and no source provided.[/bold red]")
            rprint("[yellow]Usage: quadctl push [URL_OR_FILE][/yellow]")
            sys.exit(1)

    prev_doc = get_last_pushed_document()
    should_delete_prev = _resolve_prev_doc_deletion(prev_doc, clean, keep)

    async def _push():
        with console.status(f"[bold cyan]Ingesting and optimizing '{target_source}' for Quaderno..."):
            try:
                res = await tool_push_document(
                    source_url_or_path=target_source,
                    title=target_title,
                    page=page,
                    profile=profile,
                )
                rprint(f"[bold green]✓[/bold green] {res['message']}")
                if should_delete_prev:
                    await _delete_prev_doc(prev_doc)
            except Exception as e:
                rprint(f"[bold red]Failed to push document:[/bold red] {e}")
                sys.exit(1)

    run_async(_push())


@app.command()
def window(
    profile: Optional[str] = typer.Option(None, "--profile", help="Target screen ('A4' or 'A5')"),
    rotate: bool = typer.Option(True, "--rotate/--no-rotate", help="Auto-rotate landscape window 90° for full-screen portrait reading"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Automatically delete previously pushed document without asking"),
    keep: bool = typer.Option(False, "--keep", "-k", help="Keep previously pushed document without asking"),
):
    """Capture the currently active macOS window and push it to Quaderno."""
    from quaderno_companion.triggers.window import capture_active_window_pdf
    from quaderno_companion.state import get_last_pushed_document

    prev_doc = get_last_pushed_document()
    should_delete_prev = _resolve_prev_doc_deletion(prev_doc, clean, keep)

    async def _push_window():
        with console.status("[bold cyan]Capturing and optimizing active window for Quaderno..."):
            try:
                pdf_path, filename, doc_title = capture_active_window_pdf(profile_name=profile, auto_rotate=rotate)
                res = await tool_push_document(
                    source_url_or_path=str(pdf_path),
                    title=doc_title,
                    page=1,
                    profile=profile,
                )
                rprint(f"[bold green]✓[/bold green] Captured and pushed '[white]{doc_title}[/white]' to Quaderno.")

                if should_delete_prev:
                    await _delete_prev_doc(prev_doc)
            except Exception as e:
                rprint(f"[bold red]Failed to capture window:[/bold red] {e}")
                sys.exit(1)

    run_async(_push_window())


@app.command()
def preview(
    page: Optional[int] = typer.Option(None, "--page", "-p", help="Page number to open (auto-detected if omitted)"),
    summarize: bool = typer.Option(False, "--summarize", "-s", help="Summarize the Preview document into a 1-page E-ink brief"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Continuous live mirror: automatically turns Quaderno page when you navigate in Preview"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Target screen ('A4' or 'A5')"),
):
    """Push or mirror the document currently active in Apple Preview."""
    from quaderno_companion.triggers.preview import get_preview_document_info

    doc_path, detected_page = get_preview_document_info()
    if not doc_path:
        rprint("[bold red]No active document found in Apple Preview.[/bold red] Please open a PDF in Preview first.")
        sys.exit(1)

    target_page = page if page is not None else detected_page

    async def _do_preview():
        action_verb = "Summarizing" if summarize else "Ingesting"
        page_str = f" (Page {target_page})" if target_page > 1 and not summarize else ""
        with console.status(f"[bold cyan]{action_verb} active Preview document '{Path(doc_path).name}'{page_str}..."):
            try:
                if summarize:
                    res = await agent.summarize_and_push(text_or_url=doc_path)
                else:
                    res = await tool_push_document(
                        source_url_or_path=doc_path,
                        title=Path(doc_path).stem,
                        page=target_page,
                        profile=profile,
                    )
                rprint(f"[bold green]✓[/bold green] {res['message']}")
            except Exception as e:
                rprint(f"[bold red]Failed:[/bold red] {e}")
                sys.exit(1)

        if watch and not summarize:
            rprint("[bold cyan]🪞 Live Preview Mirror Active[/bold cyan] (Press Ctrl+C to stop)")
            last_doc = doc_path
            last_page = target_page
            try:
                while True:
                    await asyncio.sleep(1.5)
                    curr_doc, curr_page = get_preview_document_info()
                    if curr_doc and (curr_doc != last_doc or curr_page != last_page):
                        if curr_doc != last_doc:
                            rprint(f"[cyan]Switched document:[/cyan] {Path(curr_doc).name} (Page {curr_page})")
                            await tool_push_document(source_url_or_path=curr_doc, title=Path(curr_doc).stem, page=curr_page)
                        elif curr_page != last_page:
                            rprint(f"[cyan]Syncing page ->[/cyan] Page {curr_page}")
                            await tool_navigate_reader(action="goto", page=curr_page)
                        last_doc = curr_doc
                        last_page = curr_page
            except (KeyboardInterrupt, asyncio.CancelledError):
                rprint("\n[yellow]Stopped Preview live sync.[/yellow]")

    run_async(_do_preview())


@app.command()
def next():
    """Jump to the next page on Quaderno viewer."""
    async def _next():
        try:
            res = await tool_navigate_reader(action="next")
            rprint(f"[bold green]✓[/bold green] Page {res['details']['page']}/{res['details']['total_pages']}")
        except Exception as e:
            rprint(f"[bold red]Navigation failed:[/bold red] {e}")
            sys.exit(1)

    run_async(_next())


@app.command()
def prev():
    """Jump to the previous page on Quaderno viewer."""
    async def _prev():
        try:
            res = await tool_navigate_reader(action="prev")
            rprint(f"[bold green]✓[/bold green] Page {res['details']['page']}/{res['details']['total_pages']}")
        except Exception as e:
            rprint(f"[bold red]Navigation failed:[/bold red] {e}")
            sys.exit(1)

    run_async(_prev())


@app.command(name="goto")
def goto_page(
    page: int = typer.Argument(..., help="Page number to display"),
):
    """Jump to a specific page number on Quaderno viewer."""
    async def _goto():
        try:
            res = await tool_navigate_reader(action="goto", page=page)
            rprint(f"[bold green]✓[/bold green] Jumped to page {res['details']['page']}/{res['details']['total_pages']}")
        except Exception as e:
            rprint(f"[bold red]Navigation failed:[/bold red] {e}")
            sys.exit(1)

    run_async(_goto())


@app.command()
def status():
    """Display real-time Quaderno connection, battery, storage, and active reading state."""
    async def _status():
        with console.status("[bold cyan]Querying Quaderno status..."):
            status_obj = await device_manager.get_status()

            table = Table(title="Fujitsu Quaderno Companion Status", border_style="cyan")
            table.add_column("Property", style="bold white")
            table.add_column("Value", style="green")

            table.add_row("Pairing Status", "✓ Paired" if status_obj.is_paired else "✗ Not Paired")
            table.add_row("Connection", f"✓ Connected ({status_obj.connection_type})" if status_obj.is_connected else "✗ Disconnected")
            if status_obj.host:
                table.add_row("Active Endpoint", f"{status_obj.host}:{status_obj.port}")
            
            bat_str = f"{status_obj.battery_level}%" if status_obj.battery_level is not None else "N/A"
            if status_obj.battery_charging:
                bat_str += " (Charging)"
            table.add_row("Battery", bat_str)

            storage_str = f"{status_obj.storage_free_mb} MB free / {status_obj.storage_total_mb} MB" if status_obj.storage_free_mb else "N/A"
            table.add_row("Storage", storage_str)

            doc_title = status_obj.reading_state.title or "None"
            page_str = f"{status_obj.reading_state.current_page} / {status_obj.reading_state.total_pages}" if status_obj.reading_state.document_id else "N/A"
            table.add_row("Active Document", doc_title)
            table.add_row("Reading Page", page_str)

            console.print(table)

    run_async(_status())


@app.command()
def summarize(
    source: str = typer.Argument(..., help="URL or text to summarize and push to Quaderno"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Title for the summary brief"),
    pages: int = typer.Option(1, "--pages", "-P", help="Target summary page length (1–5)"),
    notebook_url: Optional[str] = typer.Option(None, "--notebook-url", "-u", help="Gemini Notebook (NotebookLM) URL"),
    notebook_id: Optional[str] = typer.Option(None, "--notebook-id", "-n", help="Notebook ID from NotebookLM library"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Summarizer provider ('gemini_notebook', 'gemini_api', 'rule_based', 'auto')"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="Notebook mode ('ephemeral' fresh notebook, 'shared' existing)"),
    cleanup: bool = typer.Option(True, "--cleanup/--no-cleanup", help="Automatically delete ephemeral notebook upon completion"),
):
    """Generate a high-contrast E-ink summary brief and display it on Quaderno."""
    async def _sum():
        active_mode = mode or ("shared" if notebook_url or notebook_id else settings.notebook_mode)
        provider_info = f" via {provider or settings.summarizer_provider} ({active_mode})"
        with console.status(f"[bold cyan]Synthesizing {pages}-page E-ink summary{provider_info} for '{source}'..."):
            try:
                res = await agent.summarize_and_push(
                    source,
                    title=title,
                    pages=pages,
                    notebook_url=notebook_url,
                    notebook_id=notebook_id,
                    provider=provider,
                    notebook_mode=active_mode,
                    cleanup=cleanup,
                )
                rprint(f"[bold green]✓[/bold green] {res['message']}")
            except Exception as e:
                rprint(f"[bold red]Summarization failed:[/bold red] {e}")
                sys.exit(1)

    run_async(_sum())


@app.command()
def optimize(
    input_pdf: Path = typer.Argument(..., help="Input PDF file path"),
    output_pdf: Path = typer.Argument(..., help="Output optimized PDF path"),
    profile: str = typer.Option("A4", "--profile", help="Target screen ('A4' or 'A5')"),
    dither: bool = typer.Option(False, "--dither", help="Apply 1-bit Floyd-Steinberg dithering"),
):
    """Optimize a local PDF for Quaderno hardware (margin trimming, resolution scaling, compression)."""
    if not input_pdf.exists():
        rprint(f"[bold red]Input file not found:[/bold red] {input_pdf}")
        sys.exit(1)

    from quaderno_companion.pipeline.optimizer import EinkOptimizer
    optimizer = EinkOptimizer(profile_name=profile)
    out_bytes = optimizer.optimize_pdf(
        input_data=input_pdf,
        trim_margins=True,
        dither_raster=dither,
        output_path=output_pdf,
    )
    rprint(f"[bold green]✓[/bold green] Optimized PDF saved to [cyan]{output_pdf}[/cyan] ({len(out_bytes)/1024:.1f} KB)")


@app.command(name="sync")
def sync_cmd(
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Custom local directory to sync (defaults to ~/Quaderno)"),
):
    """Run an immediate bidirectional synchronization pass with Quaderno storage."""
    from quaderno_companion.fs.syncer import QuadernoSyncer
    target_dir = path or settings.sync_dir
    syncer = QuadernoSyncer(sync_dir=target_dir)

    with console.status(f"[bold cyan]Synchronizing Quaderno mirror folder ({target_dir})..."):
        res = syncer.sync_pass()

    if res.errors:
        rprint(f"[bold red]Sync encountered errors:[/bold red] {res.errors}")

    rprint(
        f"[bold green]✓ Sync Complete[/bold green] | "
        f"[cyan]Pulled:[/cyan] {len(res.pulled)} | "
        f"[cyan]Pushed:[/cyan] {len(res.pushed)} | "
        f"[cyan]Deleted:[/cyan] {len(res.deleted)} | "
        f"[yellow]Conflicts:[/yellow] {len(res.conflicts)}"
    )


@app.command(name="open")
def open_cmd():
    """Open the local Quaderno mirror folder in the file manager."""
    import subprocess
    target_dir = settings.sync_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(target_dir)], check=False)
    else:
        # Linux / Unix fallback
        subprocess.run(["xdg-open", str(target_dir)], check=False)
    rprint(f"[bold green]✓[/bold green] Opened local Quaderno folder at [bold cyan]{target_dir}[/bold cyan]")


@app.command(name="app")
def launch_app():
    """Launch the background companion (Menu Bar app on macOS, daemon on Linux)."""
    if sys.platform == "darwin":
        from quaderno_companion.triggers.menubar import start_menubar_app
        start_menubar_app(start_server=True)
    else:
        # On Linux, run the background server daemon directly
        rprint("[bold cyan]Starting Quaderno Companion server daemon...[/bold cyan]")
        from quaderno_companion.server import start_server
        start_server(host=settings.server_host, port=settings.server_port)


@app.command(name="install-service")
def install_service():
    """Install and start the background daemon service (macOS LaunchAgent or Linux systemd user service)."""
    import subprocess
    python_bin = sys.executable

    if sys.platform == "darwin":
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist_path = plist_dir / "com.quaderno.companion.plist"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.quaderno.companion</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-m</string>
        <string>quaderno_companion.cli</string>
        <string>app</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.config/quaderno/companion.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.config/quaderno/companion.err</string>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)
        subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=False)
        rprint(f"[bold green]✓[/bold green] Installed background service to [cyan]{plist_path}[/cyan]")
        rprint("[green]The Quaderno Companion menu bar app is now running in the background and will start on login.[/green]")
    elif sys.platform.startswith("linux"):
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True, exist_ok=True)
        service_path = systemd_dir / "quaderno-companion.service"

        log_dir = Path.home() / ".config" / "quaderno"
        log_dir.mkdir(parents=True, exist_ok=True)

        service_content = f"""[Unit]
Description=Quaderno Companion Background Daemon
After=network.target

[Service]
Type=simple
ExecStart={python_bin} -m quaderno_companion.cli app
Restart=always
RestartSec=5
StandardOutput=append:{log_dir}/companion.log
StandardError=append:{log_dir}/companion.err

[Install]
WantedBy=default.target
"""
        service_path.write_text(service_content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "quaderno-companion.service"], check=False)
        rprint(f"[bold green]✓[/bold green] Installed systemd user service to [cyan]{service_path}[/cyan]")
        rprint("[green]The Quaderno Companion daemon is now running and will auto-start on login.[/green]")
    else:
        rprint("[yellow]Background service auto-installation is supported on macOS and Linux.[/yellow]")


@app.command(name="uninstall-service")
def uninstall_service():
    """Uninstall the background service (macOS LaunchAgent or Linux systemd user service)."""
    import subprocess

    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.quaderno.companion.plist"
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
            plist_path.unlink()
            rprint("[bold green]✓[/bold green] Uninstalled Quaderno background LaunchAgent.")
        else:
            rprint("[yellow]Service plist not found.[/yellow]")
    elif sys.platform.startswith("linux"):
        service_path = Path.home() / ".config" / "systemd" / "user" / "quaderno-companion.service"
        if service_path.exists():
            subprocess.run(["systemctl", "--user", "stop", "quaderno-companion.service"], check=False)
            subprocess.run(["systemctl", "--user", "disable", "quaderno-companion.service"], check=False)
            service_path.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            rprint("[bold green]✓[/bold green] Uninstalled Quaderno systemd user service.")
        else:
            rprint("[yellow]Systemd service file not found.[/yellow]")
    else:
        rprint("[yellow]Unsupported platform for service uninstall.[/yellow]")


notebook_app = typer.Typer(
    name="notebook",
    help="Manage Google Gemini Notebook (NotebookLM) connection, auth, and notebooks.",
)
app.add_typer(notebook_app)


@notebook_app.command(name="login")
def notebook_login():
    """Log in to Google Gemini Notebook (NotebookLM) via browser."""
    import subprocess
    rprint("[bold cyan]Opening Google login window for Gemini Notebook...[/bold cyan]")
    try:
        subprocess.run([sys.executable, "-m", "notebooklm", "login", "--browser", "chrome"], check=True)
        rprint("[bold green]✓ Successfully authenticated with Google NotebookLM![/bold green]")
    except Exception as e:
        rprint(f"[bold red]Login failed:[/bold red] {e}")
        sys.exit(1)


@notebook_app.command(name="status")
def notebook_status():
    """Display Gemini Notebook (NotebookLM) connection and auth health."""
    from quaderno_companion.pipeline.notebook_client import GeminiNotebookClient
    client = GeminiNotebookClient()
    storage = client.get_storage_path()
    is_auth = client.is_authenticated()

    rprint("\n[bold]Gemini Notebook (NotebookLM) Status:[/bold]")
    rprint(f"  Authenticated: {'[green]Yes[/green]' if is_auth else '[red]No (run `quadctl notebook login`)[/red]'}")
    rprint(f"  Storage File:  [cyan]{storage or 'Not found'}[/cyan]")

    if is_auth:
        async def _check_nbs():
            lib = await client.get_library_notebooks()
            nbs = lib.get("notebooks", [])
            rprint(f"  Notebooks:     [bold]{len(nbs)}[/bold] found")
            for nb in nbs[:5]:
                rprint(f"    • {nb.get('title')} ([dim]{nb.get('id')}[/dim])")
        run_async(_check_nbs())
    rprint("")


@notebook_app.command(name="list")
def notebook_list():
    """List registered notebooks in user's Gemini Notebook account."""
    from quaderno_companion.pipeline.notebook_client import GeminiNotebookClient
    client = GeminiNotebookClient()

    async def _list():
        lib = await client.get_library_notebooks()
        nbs = lib.get("notebooks", [])
        if not nbs:
            rprint("[yellow]No notebooks found or not authenticated. Run `quadctl notebook login` first.[/yellow]")
            return

        table = Table(title="Google Gemini Notebooks")
        table.add_column("Title", style="bold white")
        table.add_column("ID", style="cyan")
        table.add_column("Sources", justify="right")

        for nb in nbs:
            table.add_row(
                str(nb.get("title", "Untitled")),
                str(nb.get("id")),
                str(nb.get("sources_count", "-")),
            )
        console.print(table)

    run_async(_list())


@app.command(name="logs")
def view_logs(
    lines: int = typer.Option(50, "-n", "--lines", help="Number of lines to show from the log file"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow log output in real-time"),
    clear: bool = typer.Option(False, "--clear", help="Clear the companion log file"),
):
    """View or tail the Quaderno Companion application logs."""
    log_file = settings.log_file
    if clear:
        if log_file.exists():
            log_file.write_text("")
            rprint("[bold green]✓[/bold green] Cleared companion log file.")
        else:
            rprint("[yellow]Log file does not exist yet.[/yellow]")
        return

    if not log_file.exists():
        rprint(f"[yellow]No log file found at {log_file}. Run the companion daemon or menubar app to generate logs.[/yellow]")
        return

    if follow:
        rprint(f"[bold cyan]Tailing {log_file} (Ctrl+C to stop)...[/bold cyan]")
        import time
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f_lines = f.readlines()
                for l in f_lines[-lines:]:
                    print(l, end="")
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            rprint("\n[dim]Stopped tailing logs.[/dim]")
    else:
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f_lines = f.readlines()
                for l in f_lines[-lines:]:
                    print(l, end="")
        except Exception as e:
            rprint(f"[red]Could not read log file: {e}[/red]")


if __name__ == "__main__":
    from quaderno_companion.config import setup_logging
    setup_logging(log_level="INFO", enable_file_logging=True, enable_console_logging=False)
    app()


