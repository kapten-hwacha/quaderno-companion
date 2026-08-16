"""Low-Level Quaderno / DPT-RP1 REST API Client Wrapper.

Manages HTTPS communication, client certificate authentication, document transfers,
and viewer control commands via dpt-rp1-py.
"""

import asyncio
import io
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import requests
from dptrp1.dptrp1 import DigitalPaper

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)

# Global re-entrant lock to prevent concurrent challenge-response nonce races against Quaderno hardware
_DEVICE_AUTH_LOCK = threading.RLock()


class DeviceNotConnectedError(Exception):
    """Raised when no communication can be established with the Quaderno."""
    pass


class DeviceNotPairedError(Exception):
    """Raised when client certificates (deviceid.dat, key.pem) are missing."""
    pass


class QuadernoClient:
    """Async-friendly wrapper for DigitalPaper protocol."""

    def __init__(self, host: str, port: int = 8443):
        self.host = host
        self.port = port
        self._dp: Optional[DigitalPaper] = None
        self._is_authenticated = False
        self._lock = threading.RLock()

    @property
    def is_authenticated(self) -> bool:
        """Check if client currently has an active authenticated session."""
        return self._is_authenticated

    @property
    def has_credentials(self) -> bool:
        """Check if SSL client credentials exist on disk."""
        return settings.device_id_path.exists() and settings.device_key_path.exists()

    def _ensure_dp_instance(self) -> DigitalPaper:
        """Initialize or return the DigitalPaper client instance."""
        if not self.has_credentials:
            raise DeviceNotPairedError(
                f"Quaderno credentials missing. Expected {settings.device_id_path} and {settings.device_key_path}. "
                f"Run `quadctl pair` to register with your Quaderno PIN."
            )

        if self._dp is None:
            from requests.adapters import HTTPAdapter
            from urllib3.util import Retry

            clean_host = self.host.split(":")[0] if self.host else self.host
            dp = DigitalPaper(addr=clean_host)

            # Prevent stale keep-alive socket freezes on Quaderno embedded server
            retries = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retries, pool_connections=1, pool_maxsize=1)
            dp.session.mount("https://", adapter)
            dp.session.mount("http://", adapter)
            dp.session.headers.update({"Connection": "close"})

            orig_send = dp.session.send
            lock = self._lock

            def _thread_safe_send(request, **kwargs):
                if "timeout" not in kwargs or kwargs.get("timeout") is None:
                    is_file = "file" in getattr(request, "url", "")
                    kwargs["timeout"] = 30.0 if is_file else 10.0
                with lock:
                    return orig_send(request, **kwargs)

            setattr(dp.session, "send", _thread_safe_send)
            self._dp = dp
        return self._dp

    def _perform_auth(self, dp: DigitalPaper, client_id: str, key_data: str) -> None:
        """Perform SSL challenge-response authentication with robust error checking."""
        import time
        import httpsig

        for attempt in range(2):
            try:
                sig_maker = httpsig.Signer(secret=key_data, algorithm="rsa-sha256")
                nonce = dp._get_nonce(client_id)
                signed_nonce = sig_maker.sign(nonce)
                data = {"client_id": client_id, "nonce_signed": signed_nonce}
                r = dp._put_endpoint("/auth", data=data)

                if r.status_code in (200, 204):
                    cookie_header = r.headers.get("Set-Cookie") or r.headers.get("set-cookie")
                    if not cookie_header:
                        raise DeviceNotConnectedError(
                            "Quaderno did not return a session cookie. "
                            "Ensure the Quaderno screen is awake and unlocked."
                        )

                    _, credentials = cookie_header.split("; ")[0].split("=")
                    dp.session.cookies["Credentials"] = credentials
                    return

                if r.status_code in (401, 403):
                    if attempt == 0:
                        logger.debug(
                            f"Quaderno auth returned HTTP {r.status_code} (potential transient nonce race). "
                            f"Retrying immediately with fresh nonce..."
                        )
                        time.sleep(0.2)
                        continue
                    raise DeviceNotPairedError(
                        f"Quaderno authentication rejected (HTTP {r.status_code}). "
                        f"Your device pairing credentials may be stale or replaced. "
                        f"Please run `quadctl pair` to pair again."
                    )

                raise DeviceNotConnectedError(
                    f"Quaderno auth endpoint returned HTTP {r.status_code}: {r.text}"
                )
            except (DeviceNotPairedError, DeviceNotConnectedError):
                raise
            except KeyError as ke:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                raise DeviceNotConnectedError(
                    f"Quaderno authentication failed (missing session cookie '{ke}'). "
                    f"Ensure the device screen is awake and unlocked, or run `quadctl pair`."
                ) from ke
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                raise DeviceNotConnectedError(f"Authentication error with Quaderno: {e}") from e

    async def authenticate(self) -> bool:
        """Authenticate SSL session with the Quaderno device."""
        return await asyncio.to_thread(self.authenticate_sync)

    async def register(self, pin: Optional[str] = None) -> Tuple[str, str]:
        """Perform one-time pairing with the device.

        If pin is provided, supplies it during the Diffie-Hellman handshake.
        Returns:
            Tuple of (device_id, key_pem) strings.
        """
        settings.ensure_directories()
        
        clean_host = self.host.split(":")[0] if self.host else self.host
        dp = DigitalPaper(addr=clean_host)

        def _reg():
            import time
            try:
                dp._reg_endpoint_request("PUT", "/register/cleanup")
                time.sleep(1.0)
            except Exception:
                pass

            if pin:
                import unittest.mock
                with unittest.mock.patch("builtins.input", return_value=str(pin).strip()):
                    result = dp.register()
            else:
                result = dp.register()

            if not result or len(result) < 3:
                raise RuntimeError(
                    "Registration handshake failed. Ensure the Quaderno screen is awake and unlocked, "
                    "and close any official Sony/Fujitsu Digital Paper apps that might be running."
                )

            ca_cert, priv_key, client_id = result
            return client_id, priv_key

        client_id, key = await asyncio.to_thread(_reg)

        # Persist credentials with restrictive owner-only permissions (0600)
        import os
        settings.device_id_path.write_text(client_id.strip())
        os.chmod(settings.device_id_path, 0o600)
        settings.device_key_path.write_text(key.strip())
        os.chmod(settings.device_key_path, 0o600)
        if clean_host:
            settings.device_ip = clean_host
            try:
                Path(".env").write_text(f"QUADERNO_DEVICE_IP={clean_host}\n")
            except Exception:
                pass
        logger.info(f"Saved Quaderno credentials securely to {settings.config_dir}")

        # Reset instance to use new credentials
        self._dp = None
        self._is_authenticated = False
        return client_id, key

    async def ping(self) -> bool:
        """Ping device to check responsiveness."""
        if not self.has_credentials:
            return False
        try:
            dp = self._ensure_dp_instance()
            return await asyncio.to_thread(dp.ping)
        except Exception:
            return False

    def _is_auth_or_conn_error(self, e: Exception) -> bool:
        """Check if an exception is caused by session expiry, 401/403 HTTP error, or connection reset."""
        resp = getattr(e, "response", None)
        if resp is not None and getattr(resp, "status_code", None) in (401, 403, 502, 503, 504):
            return True
        err_str = str(e).lower()
        return any(
            x in err_str
            for x in (
                "authentication",
                "401",
                "unauthorized",
                "forbidden",
                "timeout",
                "timed out",
                "connection reset",
                "broken pipe",
                "session",
            )
        )

    def _run_with_reauth(self, fn):
        """Execute a function with automatic re-authentication if session expired or timed out."""
        try:
            return fn()
        except Exception as e:
            if self._is_auth_or_conn_error(e):
                logger.info(f"Quaderno connection/session issue ({str(e)[:60]}). Re-authenticating...")
                try:
                    self.authenticate_sync()
                    return fn()
                except Exception as retry_err:
                    logger.error(f"Re-auth retry failed: {retry_err}")
                    raise
            raise

    async def upload_document(
        self,
        pdf_data: Union[bytes, io.BytesIO],
        remote_filename: str,
        remote_folder: str = "Document/Companion",
    ) -> Dict[str, Any]:
        """Upload a PDF document to Quaderno internal storage with auto re-auth."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()
        remote_path = f"{remote_folder.strip('/')}/{remote_filename.lstrip('/')}"

        def _upload():
            if isinstance(pdf_data, bytes):
                stream = io.BytesIO(pdf_data)
            else:
                stream = pdf_data
                stream.seek(0)

            def _do_upload():
                stream.seek(0)
                dp.upload(stream, remote_path)

            self._run_with_reauth(_do_upload)

            # Resolve document info
            try:
                info = dp.list_document_info(remote_path)
                return info or {"entry_id": dp._get_object_id(remote_path), "entry_path": remote_path}
            except Exception:
                doc_id = dp._get_object_id(remote_path)
                return {"entry_id": doc_id, "document_id": doc_id, "entry_path": remote_path}

        result = await asyncio.to_thread(_upload)
        logger.info(f"Uploaded document to Quaderno: {remote_path}")
        return result

    async def display_document(self, document_id: str, page: int = 1) -> None:
        """Instruct Quaderno to open and display a document at a specific page."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()

        def _display():
            self._run_with_reauth(lambda: dp.display_document(document_id=document_id, page=page))

        await asyncio.to_thread(_display)
        logger.info(f"Quaderno viewer opened document_id={document_id} at page={page}")

    async def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents currently stored on the Quaderno."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()

        def _list():
            return self._run_with_reauth(lambda: dp.list_documents())

        docs = await asyncio.to_thread(_list)
        return docs or []

    async def resolve_document_id(self, remote_path: str) -> Optional[str]:
        """Look up document_id by its remote file path."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()

        def _resolve():
            try:
                info = self._run_with_reauth(lambda: dp.list_document_info(remote_path))
                return info.get("entry_id") or info.get("document_id")
            except Exception:
                return None

        return await asyncio.to_thread(_resolve)

    async def get_battery_status(self) -> Dict[str, Any]:
        """Fetch battery level and charging state."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()
        return await asyncio.to_thread(lambda: self._run_with_reauth(dp.get_battery))

    async def get_storage_status(self) -> Dict[str, Any]:
        """Fetch internal storage capacity and free space."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()
        return await asyncio.to_thread(lambda: self._run_with_reauth(dp.get_storage))

    async def get_recent_document(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recently read document metadata directly from Quaderno hardware."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()

        def _get():
            try:
                r = self._run_with_reauth(lambda: dp._get_endpoint("/documents2?limit=1&sort_by=reading_date"))
                if r.status_code == 200:
                    entries = r.json().get("entry_list", [])
                    return entries[0] if entries else None
            except Exception:
                pass
            return None

        return await asyncio.to_thread(_get)

    async def take_screenshot(self) -> bytes:
        """Capture live screenshot of Quaderno screen as JPEG bytes."""
        if not self._is_authenticated:
            await self.authenticate()

        dp = self._ensure_dp_instance()

        def _snap():
            r = self._run_with_reauth(lambda: dp._get_endpoint("/system/controls/screen_shot2?query=jpeg"))
            return r.content

        return await asyncio.to_thread(_snap)

    def authenticate_sync(self) -> bool:
        """Authenticate SSL session synchronously with the Quaderno device."""
        if not self.has_credentials:
            raise DeviceNotPairedError("Device not paired.")

        with _DEVICE_AUTH_LOCK:
            if self._is_authenticated and self._dp is not None:
                return True

            dp = self._ensure_dp_instance()
            try:
                device_id = settings.device_id_path.read_text().strip()
                key_data = settings.device_key_path.read_text().strip()
                self._perform_auth(dp, device_id, key_data)
                self._is_authenticated = True
                logger.info(f"Authenticated session with Quaderno at {self.host}")
                return True
            except Exception as e:
                self._is_authenticated = False
                self._dp = None  # Reset session adapter on auth failure
                logger.error(f"Authentication failed with Quaderno: {e}")
                raise e

    def _run_sync_with_reauth(self, fn):
        """Execute a synchronous callable, re-authenticating once on auth/session errors.

        Mirrors the async `_run_with_reauth` used by async methods above.
        """
        if not self._is_authenticated:
            self.authenticate_sync()
        try:
            return fn()
        except Exception as e:
            if self._is_auth_or_conn_error(e):
                logger.info(f"Sync session expired ({str(e)[:60]}). Re-authenticating...")
                self.authenticate_sync()
                return fn()
            raise

    def download_document(self, document_id_or_path: str) -> bytes:
        """Download raw PDF document bytes by document entry ID or remote path (synchronous/thread-safe)."""
        dp = self._ensure_dp_instance()

        def _do():
            # If target is a UUID entry_id, fetch directly from document file endpoint
            if len(document_id_or_path) == 36 and "-" in document_id_or_path:
                resp = dp._get_endpoint(f"/documents/{document_id_or_path}/file")
                return resp.content
            return dp.download(document_id_or_path)

        return self._run_sync_with_reauth(_do)

    def delete_document(self, document_id_or_path: str) -> bool:
        """Delete a document from Quaderno (synchronous/thread-safe)."""
        dp = self._ensure_dp_instance()

        def _do():
            try:
                dp.delete_document_by_id(document_id_or_path)
            except Exception:
                dp.delete_document(document_id_or_path)
            return True

        self._run_sync_with_reauth(_do)
        return True

    def list_all_documents(self) -> List[Dict[str, Any]]:
        """List all documents on Quaderno (synchronous/thread-safe with auto re-auth)."""
        dp = self._ensure_dp_instance()
        return self._run_sync_with_reauth(lambda: dp.list_documents() or [])

    def upload_document_sync(
        self,
        pdf_data: Union[bytes, io.BytesIO, Path, str],
        filename: Optional[str] = None,
        folder: str = "Document",
    ) -> str:
        """Upload a PDF document synchronously and return its entry ID."""
        dp = self._ensure_dp_instance()

        if isinstance(pdf_data, (str, Path)):
            path_obj = Path(pdf_data)
            target_filename = filename or path_obj.name
            stream = io.BytesIO(path_obj.read_bytes())
        elif isinstance(pdf_data, bytes):
            target_filename = filename or "document.pdf"
            stream = io.BytesIO(pdf_data)
        else:
            target_filename = filename or "document.pdf"
            stream = pdf_data

        clean_folder = folder.strip("/")
        if not clean_folder:
            clean_folder = "Document"
        elif clean_folder != "Document" and not clean_folder.lower().startswith("document/"):
            clean_folder = f"Document/{clean_folder}"

        remote_path = f"{clean_folder}/{target_filename.lstrip('/')}"

        def _do_upload():
            stream.seek(0)
            dp.upload(stream, remote_path)

        self._run_sync_with_reauth(_do_upload)

        try:
            info = dp.list_document_info(remote_path)
            entry_id = info.get("entry_id") or info.get("document_id") or dp._get_object_id(remote_path)
            logger.info(f"Successfully uploaded document to Quaderno: {remote_path} (ID: {entry_id})")
            return entry_id
        except Exception:
            entry_id = dp._get_object_id(remote_path)
            logger.info(f"Uploaded document to Quaderno: {remote_path} (ID: {entry_id})")
            return entry_id

    def create_folder_sync(self, remote_path: str) -> None:
        """Create a new folder on Quaderno (synchronous/thread-safe)."""
        dp = self._ensure_dp_instance()
        clean_path = remote_path.strip("/")
        self._run_sync_with_reauth(lambda: dp.new_folder(clean_path))

    def delete_folder_sync(self, remote_path_or_id: str) -> bool:
        """Delete a folder from Quaderno (synchronous/thread-safe)."""
        dp = self._ensure_dp_instance()

        def _do():
            try:
                dp.delete_folder_by_id(remote_path_or_id)
            except Exception:
                dp.delete_folder(remote_path_or_id.strip("/"))
            return True

        self._run_sync_with_reauth(_do)
        return True

    def get_standby_timeout(self) -> str:
        """Get the device standby/sleep timeout (e.g. '10', 'never')."""
        dp = self._ensure_dp_instance()
        try:
            resp = self._run_sync_with_reauth(
                lambda: dp._get_endpoint("/system/configs/timeout_to_standby")
            )
            return str(resp.json().get("value", "10"))
        except Exception as e:
            logger.warning(f"Failed to get standby timeout: {e}")
            return "10"

    def set_standby_timeout(self, timeout_value: str) -> bool:
        """Set the device standby/sleep timeout (e.g. 'never' to keep awake, '10' for 10 minutes)."""
        dp = self._ensure_dp_instance()
        try:
            resp = self._run_sync_with_reauth(
                lambda: dp._put_endpoint(
                    "/system/configs/timeout_to_standby", data={"value": str(timeout_value)}
                )
            )
            logger.info(f"Set Quaderno standby timeout to '{timeout_value}' (Status {resp.status_code})")
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.warning(f"Failed to set standby timeout to {timeout_value}: {e}")
            return False

