"""Unit tests for Linux platform compatibility and fallbacks."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from quaderno_companion.cli import app
from quaderno_companion.device.router import NetworkRouter
from quaderno_companion.triggers.browser import (
    get_firefox_profile_dirs,
    get_firefox_window_title,
    get_frontmost_app_name,
)
from quaderno_companion.triggers.preview import notify, prompt_delete_previous_dialog, prompt_text_dialog, show_alert
from quaderno_companion.triggers.window import _capture_screen_to_file


def test_linux_router_ip_route_gateway():
    """Test default gateway parsing from Linux `ip route` output."""
    router = NetworkRouter()
    mock_ip_out = "default via 192.168.43.1 dev wlan0 proto dhcp src 192.168.43.50 metric 600\n"

    with patch("subprocess.check_output", return_value=mock_ip_out):
        gateways = router._get_default_gateways()
        assert "192.168.43.1" in gateways


def test_linux_router_proc_net_route_gateway(tmp_path):
    """Test default gateway parsing from /proc/net/route."""
    router = NetworkRouter()
    # 012BA8C0 in little-endian hex = 192.168.43.1 (C0.A8.2B.01)
    proc_content = "Iface\tDestination\tGateway\tFlags\nwlan0\t00000000\t012BA8C0\t0003\n"
    fake_proc = tmp_path / "route"
    fake_proc.write_text(proc_content)

    with patch("subprocess.check_output", side_effect=FileNotFoundError), \
         patch("pathlib.Path.exists", side_effect=lambda: True), \
         patch("pathlib.Path.read_text", return_value=proc_content):
        gateways = router._get_default_gateways()
        assert "192.168.43.1" in gateways


def test_linux_firefox_profile_discovery(tmp_path):
    """Test discovery of Firefox profiles in ~/.mozilla/firefox."""
    mozilla_dir = tmp_path / ".mozilla" / "firefox"
    prof_dir = mozilla_dir / "abc12345.default-release"
    prof_dir.mkdir(parents=True)
    (prof_dir / "places.sqlite").touch()

    with patch("pathlib.Path.home", return_value=tmp_path):
        dirs = get_firefox_profile_dirs()
        assert any(".mozilla/firefox" in str(d) for d in dirs)


def test_linux_firefox_window_title_xdotool():
    """Test Firefox window title resolution via xdotool on Linux."""
    with patch("sys.platform", "linux"), \
         patch("subprocess.check_output", return_value="GitHub - Mozilla Firefox\n"):
        title = get_firefox_window_title()
        assert title == "GitHub - Mozilla Firefox"


def test_linux_frontmost_app_xdotool():
    """Test frontmost application name resolution via xdotool and /proc."""
    with patch("sys.platform", "linux"), \
         patch("quaderno_companion.triggers.browser.AppKit", None), \
         patch("subprocess.check_output", return_value="12345\n"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="firefox\n"):
        name = get_frontmost_app_name()
        assert name == "firefox"


def test_linux_notify_send():
    """Test that notify uses notify-send on Linux."""
    with patch("sys.platform", "linux"), \
         patch("subprocess.run") as mock_run:
        notify("Quaderno", "Sync Complete", "3 documents synced")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert args[1] == "Quaderno"
        assert "Sync Complete" in args[2]


def test_linux_dialogs_zenity():
    """Test alert and prompt dialogs on Linux via zenity."""
    with patch("sys.platform", "linux"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="user_input_page\n")

        # Alert
        show_alert("Alert Title", "Alert Message")
        assert mock_run.call_args[0][0][0] == "zenity"

        # Prompt text
        res = prompt_text_dialog("Go to Page", "Enter page number:", "1")
        assert res == "user_input_page"

        # Prompt delete previous
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        del_res = prompt_delete_previous_dialog("Previous Document")
        assert del_res is True


def test_linux_screen_capture_tools(tmp_path):
    """Test Linux screen capture fallbacks (grim, maim, scrot, import)."""
    target = str(tmp_path / "test.png")
    with patch("sys.platform", "linux"):
        # Wayland grim success
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            ok = _capture_screen_to_file(target)
            assert ok is True
            assert mock_run.call_args[0][0][0] == "grim"


def test_linux_install_uninstall_service(tmp_path):
    """Test systemd user service creation and deletion on Linux."""
    runner = CliRunner()

    with patch("sys.platform", "linux"), \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_run:
        # Install
        res = runner.invoke(app, ["install-service"])
        assert res.exit_code == 0
        service_file = tmp_path / ".config" / "systemd" / "user" / "quaderno-companion.service"
        assert service_file.exists()
        content = service_file.read_text()
        assert "Quaderno Companion Background Daemon" in content
        assert "ExecStart=" in content
        assert "Restart=always" in content

        # Uninstall
        res_uninst = runner.invoke(app, ["uninstall-service"])
        assert res_uninst.exit_code == 0
        assert not service_file.exists()
