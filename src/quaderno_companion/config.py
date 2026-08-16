"""Configuration management for Quaderno Companion."""

from pathlib import Path
from typing import Dict, Literal, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScreenProfile(BaseModel):
    """E-Ink Screen hardware specifications."""
    name: str
    width: int  # Native pixels width
    height: int  # Native pixels height
    dpi: int
    diagonal_inch: float


SCREEN_PROFILES: Dict[str, ScreenProfile] = {
    "A4": ScreenProfile(
        name="Fujitsu Quaderno A4 Gen 2",
        width=1650,
        height=2200,
        dpi=207,
        diagonal_inch=13.3,
    ),
    "A5": ScreenProfile(
        name="Fujitsu Quaderno A5 Gen 2",
        width=1404,
        height=1872,
        dpi=227,
        diagonal_inch=10.3,
    ),
}


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="QUADERNO_",
        env_file=(str(Path.home() / ".config" / "quaderno" / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Device Network Configuration
    device_ip: Optional[str] = Field(
        default=None,
        description="Explicit static IP for Wi-Fi connection. If None, auto-routing probes hostname/network.",
    )
    device_wifi_host: str = Field(
        default="digitalpaper.local",
        description="mDNS or hostname for Quaderno on local Wi-Fi.",
    )
    device_bluetooth_gateway: str = Field(
        default="192.168.128.1",
        description="Default gateway IP for Bluetooth PAN (bnep0/en*).",
    )
    device_usb_ip: str = Field(
        default="172.25.47.1",
        description="Standard USB tethering IP for Sony DPT / Quaderno.",
    )
    device_port: int = Field(
        default=8443,
        description="HTTPS port used by the Quaderno REST API.",
    )

    # Pairing & Credential storage
    config_dir: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "quaderno",
        description="Directory storing device credentials (deviceid.dat, key.pem).",
    )
    device_id_file: str = "deviceid.dat"
    device_key_file: str = "key.pem"
    state_file: str = "state.json"

    # Default Target Screen Profile
    default_profile: Literal["A4", "A5"] = Field(
        default="A4",
        description="Target Quaderno hardware profile (A4: 1650x2200, A5: 1404x1872).",
    )

    # Quaderno Remote Storage
    remote_companion_folder: str = Field(
        default="Document/Companion",
        description="Remote folder on Quaderno storage where pushed documents are placed.",
    )

    # Local Mirror & Sync Configuration
    sync_dir: Path = Field(
        default_factory=lambda: Path.home() / "Quaderno",
        description="Local mirror directory synced with Quaderno storage.",
    )
    sync_interval_seconds: int = Field(
        default=30,
        description="Interval in seconds for periodic background sync passes.",
    )
    auto_sync_enabled: bool = Field(
        default=True,
        description="Whether background automatic folder sync is enabled.",
    )
    sync_state_file: str = Field(
        default="sync_state.json",
        description="State database file for tracking synced file hashes and mtimes.",
    )

    # Local Cache & Temp
    cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "quaderno_companion",
        description="Directory for cached downloaded/converted documents.",
    )

    # Security & API Auth
    api_key: Optional[str] = Field(
        default=None,
        description="Optional API key for daemon authentication. If set, clients must pass X-API-Key or Bearer token.",
    )

    # FastAPI Server
    server_host: str = Field(default="127.0.0.1")
    server_port: int = Field(default=5000)

    # Gemini Notebook / NotebookLM & LLM Settings for Summarization
    notebook_url: Optional[str] = Field(
        default=None,
        description="Target Gemini Notebook (NotebookLM) URL (e.g. https://notebooklm.google.com/notebook/...).",
    )
    notebook_id: Optional[str] = Field(
        default=None,
        description="Target Gemini Notebook ID from the NotebookLM library.",
    )
    notebook_skill_dir: Optional[Path] = Field(
        default=None,
        description="Custom path to NotebookLM skill directory (defaults to ~/.gemini/config/skills/notebooklm).",
    )
    notebook_storage_path: Optional[Path] = Field(
        default=None,
        description="Path to NotebookLM storage_state.json auth file.",
    )
    notebook_mode: Literal["ephemeral", "shared", "auto"] = Field(
        default="ephemeral",
        description="NotebookLM operation mode: 'ephemeral' (spawn fresh notebook + cleanup), 'shared' (query existing notebook), 'auto'.",
    )
    notebook_cleanup: bool = Field(
        default=True,
        description="Whether to automatically delete ephemeral notebooks upon summary synthesis.",
    )
    summarizer_provider: str = Field(
        default="gemini_api",
        description="Default summarizer provider ('gemini_api', 'gemini_notebook', 'rule_based', 'auto').",
    )
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="gemini-3.5-flash-lite")

    @property
    def device_id_path(self) -> Path:
        return self.config_dir / self.device_id_file

    @property
    def device_key_path(self) -> Path:
        return self.config_dir / self.device_key_file

    @property
    def state_path(self) -> Path:
        return self.config_dir / self.state_file

    @property
    def sync_state_path(self) -> Path:
        return self.config_dir / self.sync_state_file

    @property
    def active_screen_profile(self) -> ScreenProfile:
        return SCREEN_PROFILES.get(self.default_profile, SCREEN_PROFILES["A4"])

    def ensure_directories(self) -> None:
        """Create necessary config, cache, and sync directories with safe permissions."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.chmod(self.config_dir, 0o700)
            os.chmod(self.cache_dir, 0o700)
        except Exception:
            pass

    def clean_cache(self, max_age_days: int = 7, max_total_mb: int = 200) -> int:
        """Remove stale files from cache directory older than max_age_days or exceeding max_total_mb.
        
        Returns the number of deleted files.
        """
        if not self.cache_dir.exists():
            return 0

        import time
        now = time.time()
        max_age_sec = max_age_days * 86400
        deleted_count = 0

        try:
            files = [f for f in self.cache_dir.iterdir() if f.is_file()]
            # 1. Delete files older than max_age_days
            remaining_files = []
            for f in files:
                try:
                    stat = f.stat()
                    if (now - stat.st_mtime) > max_age_sec:
                        f.unlink(missing_ok=True)
                        deleted_count += 1
                    else:
                        remaining_files.append((f, stat.st_size, stat.st_mtime))
                except Exception:
                    pass

            # 2. If remaining files exceed max_total_mb, delete oldest first
            total_bytes = sum(size for _, size, _ in remaining_files)
            max_bytes = max_total_mb * 1024 * 1024
            if total_bytes > max_bytes:
                # Sort by mtime ascending (oldest first)
                remaining_files.sort(key=lambda x: x[2])
                for f, size, _ in remaining_files:
                    try:
                        f.unlink(missing_ok=True)
                        deleted_count += 1
                        total_bytes -= size
                        if total_bytes <= max_bytes:
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        return deleted_count


# Global settings instance
settings = Settings()
