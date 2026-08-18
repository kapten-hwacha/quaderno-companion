"""Tests for Quaderno device manager and router."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from quaderno_companion.device.client import DeviceNotPairedError, QuadernoClient
from quaderno_companion.device.manager import QuadernoDeviceManager, ReadingState
from quaderno_companion.device.router import DeviceRoute, NetworkRouter


@pytest.mark.asyncio
async def test_router_candidate_probing():
    """Verify router tests candidates and returns the first reachable route."""
    router = NetworkRouter()
    
    # Mock probe to succeed on bluetooth_pan candidate
    async def mock_probe(host, port, timeout=1.0):
        return host == "192.168.128.1"

    with patch.object(router, "_probe_endpoint", side_effect=mock_probe):
        route = await router.get_active_route(force_refresh=True)
        assert route is not None
        assert route.connection_type == "bluetooth_pan"
        assert route.host == "192.168.128.1"


@pytest.mark.asyncio
async def test_router_wifi_ap_probing():
    """Verify router detects Quaderno SoftAP gateway 192.168.43.1."""
    router = NetworkRouter()

    async def mock_probe(host, port, timeout=1.0):
        return host == "192.168.43.1"

    with patch.object(router, "_probe_endpoint", side_effect=mock_probe):
        route = await router.get_active_route(force_refresh=True)
        assert route is not None
        assert route.connection_type == "wifi_ap"
        assert route.host == "192.168.43.1"



from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

@pytest.mark.asyncio
async def test_unpaired_device_raises_error():
    """Verify that calling get_client on an unpaired device raises DeviceNotPairedError."""
    mgr = QuadernoDeviceManager()
    with patch.object(QuadernoDeviceManager, "is_paired", new_callable=PropertyMock, return_value=False):
        assert not mgr.is_paired
        with pytest.raises(DeviceNotPairedError):
            await mgr.get_client()


@pytest.mark.asyncio
async def test_reading_state_navigation():
    """Verify navigation commands properly update reading state."""
    mgr = QuadernoDeviceManager()
    mgr._reading_state = ReadingState(
        document_id="doc-12345",
        title="Test Document",
        current_page=2,
        total_pages=5,
    )

    mock_client = MagicMock()
    mock_client.display_document = AsyncMock()

    with patch.object(mgr, "get_client", return_value=mock_client):
        # Test 'next'
        res_next = await mgr.navigate("next")
        assert res_next["page"] == 3
        assert mgr._reading_state.current_page == 3
        mock_client.display_document.assert_called_with(document_id="doc-12345", page=3)

        # Test 'prev'
        res_prev = await mgr.navigate("prev")
        assert res_prev["page"] == 2
        assert mgr._reading_state.current_page == 2

        # Test 'goto'
        res_goto = await mgr.navigate("goto", page=5)
        assert res_goto["page"] == 5
        assert mgr._reading_state.current_page == 5

        # Test upper bound clamp
        res_clamp = await mgr.navigate("next")
        assert res_clamp["page"] == 5


@pytest.mark.asyncio
async def test_concurrent_get_client_calls_serialize():
    """Verify that multiple concurrent async/sync get_client calls only trigger a single authentication."""
    import asyncio
    import concurrent.futures

    mgr = QuadernoDeviceManager()
    mgr._client = None
    mgr._current_route = None

    mock_route = DeviceRoute(host="192.168.1.100", port=8443, connection_type="wifi", is_reachable=True)
    auth_call_count = 0

    def mock_auth_sync(self):
        nonlocal auth_call_count
        auth_call_count += 1
        self._is_authenticated = True
        return True

    with patch.object(QuadernoDeviceManager, "is_paired", new_callable=PropertyMock, return_value=True), \
         patch.object(NetworkRouter, "get_active_route_sync", return_value=mock_route), \
         patch.object(NetworkRouter, "_probe_endpoint_sync", return_value=True), \
         patch.object(QuadernoClient, "authenticate_sync", mock_auth_sync):

        # Launch 5 concurrent calls across thread pool and event loop
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [
                mgr.get_client(),
                mgr.get_client(),
                loop.run_in_executor(executor, mgr.get_client_sync),
                loop.run_in_executor(executor, mgr.get_client_sync),
                mgr.get_client(),
            ]
            clients = await asyncio.gather(*tasks)

        assert len(clients) == 5
        # All callers should receive the exact same authenticated client instance
        first_client = clients[0]
        assert all(c is first_client for c in clients)
        assert first_client.is_authenticated
        assert auth_call_count == 1


def test_perform_auth_retries_on_transient_401():
    """Verify that _perform_auth retries with a fresh nonce upon initial HTTP 401 and succeeds."""
    client = QuadernoClient(host="192.168.1.100")
    mock_dp = MagicMock()
    mock_dp._get_nonce.side_effect = ["nonce-1", "nonce-2"]

    # First PUT /auth returns 401 (transient collision), second returns 200 (success)
    resp_401 = MagicMock()
    resp_401.status_code = 401

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {"Set-Cookie": "Credentials=test_session_token; Path=/"}

    mock_dp._put_endpoint.side_effect = [resp_401, resp_200]
    mock_dp.session.cookies = {}

    with patch("httpsig.Signer") as mock_signer_cls, \
         patch("time.sleep"):
        mock_signer = MagicMock()
        mock_signer.sign.side_effect = lambda n: f"signed-{n}"
        mock_signer_cls.return_value = mock_signer

        client._perform_auth(mock_dp, client_id="test-client-id", key_data="test-key")

        assert mock_dp._get_nonce.call_count == 2
        assert mock_dp._put_endpoint.call_count == 2
        assert mock_dp.session.cookies.get("Credentials") == "test_session_token"


def test_perform_auth_raises_on_persistent_401():
    """Verify that _perform_auth raises DeviceNotPairedError if HTTP 401 persists after retry."""
    client = QuadernoClient(host="192.168.1.100")
    mock_dp = MagicMock()
    mock_dp._get_nonce.return_value = "nonce-stale"

    resp_401 = MagicMock()
    resp_401.status_code = 401
    mock_dp._put_endpoint.return_value = resp_401

    with patch("httpsig.Signer") as mock_signer_cls, \
         patch("time.sleep"):
        mock_signer = MagicMock()
        mock_signer.sign.return_value = "signed-nonce"
        mock_signer_cls.return_value = mock_signer

        with pytest.raises(DeviceNotPairedError) as exc_info:
            client._perform_auth(mock_dp, client_id="test-client-id", key_data="test-key")

        assert "HTTP 401" in str(exc_info.value)
        assert mock_dp._get_nonce.call_count == 2


@pytest.mark.asyncio
async def test_get_status_fast_disconnect_detection():
    """Verify that get_status returns is_connected=False and clears client cache when device is offline."""
    from quaderno_companion.device.client import DeviceNotConnectedError

    mgr = QuadernoDeviceManager()
    mock_client = MagicMock()
    mock_client.get_battery_status = AsyncMock(side_effect=DeviceNotConnectedError("Connection timed out"))
    mock_client.get_storage_status = AsyncMock(side_effect=DeviceNotConnectedError("Connection timed out"))
    mock_client.get_recent_document = AsyncMock(side_effect=DeviceNotConnectedError("Connection timed out"))

    with patch.object(QuadernoDeviceManager, "is_paired", new_callable=PropertyMock, return_value=True), \
         patch.object(mgr, "get_client", return_value=mock_client):
        mgr._client = mock_client
        mgr._current_route = DeviceRoute(host="192.168.1.100", port=8443, connection_type="wifi", is_reachable=True)

        status = await mgr.get_status()
        assert status.is_connected is False
        assert mgr._client is None
        assert mgr._current_route is None


def test_get_client_sync_invalidates_stale_route():
    """Verify get_client_sync invalidates cached client when socket probe on current route fails."""
    mgr = QuadernoDeviceManager()
    stale_client = MagicMock()
    stale_client.is_authenticated = True
    mgr._client = stale_client
    mgr._current_route = DeviceRoute(host="192.168.1.100", port=8443, connection_type="wifi", is_reachable=True)

    fresh_route = DeviceRoute(host="192.168.128.1", port=8443, connection_type="bluetooth_pan", is_reachable=True)

    with patch.object(QuadernoDeviceManager, "is_paired", new_callable=PropertyMock, return_value=True), \
         patch.object(mgr.router, "_probe_endpoint_sync", return_value=False), \
         patch.object(mgr.router, "get_active_route_sync", return_value=fresh_route), \
         patch.object(QuadernoClient, "authenticate_sync", return_value=True):

        client = mgr.get_client_sync()
        assert client is not stale_client
        assert mgr._current_route == fresh_route


def test_client_run_with_reauth_fails_fast_on_network_error():
    """Verify _run_with_reauth raises DeviceNotConnectedError immediately on network timeout without re-auth."""
    from quaderno_companion.device.client import DeviceNotConnectedError

    client = QuadernoClient(host="192.168.1.100")
    client._is_authenticated = True

    def failing_call():
        raise TimeoutError("Connection timed out (Read timed out)")

    with patch.object(client, "authenticate_sync") as mock_auth:
        with pytest.raises(DeviceNotConnectedError) as exc_info:
            client._run_with_reauth(failing_call)

        assert "connection lost" in str(exc_info.value).lower()
        mock_auth.assert_not_called()
        assert client._is_authenticated is False


def test_router_subnet_scan_sync_finds_device():
    """Verify that get_active_route_sync discovers device on local subnet when candidate list fails."""
    router = NetworkRouter()

    with patch.object(router, "_build_candidate_list", return_value=[]), \
         patch.object(router, "_get_local_subnet_prefix", return_value=("192.168.1.50", "192.168.1.")), \
         patch.object(router, "_probe_endpoint_sync", side_effect=lambda ip, port, timeout: ip == "192.168.1.120"), \
         patch.object(router, "_verify_quaderno_endpoint_sync", side_effect=lambda ip, port: ip == "192.168.1.120"):

        route = router.get_active_route_sync(force_refresh=True)
        assert route is not None
        assert route.host == "192.168.1.120"
        assert route.connection_type == "wifi"


@pytest.mark.asyncio
async def test_get_client_async_reconnects_when_online():
    """Verify that get_client discovers route and authenticates when device comes online."""
    mgr = QuadernoDeviceManager()
    mgr._client = None
    mgr._current_route = None

    discovered_route = DeviceRoute(host="192.168.1.77", port=8443, connection_type="wifi", is_reachable=True)

    with patch.object(QuadernoDeviceManager, "is_paired", new_callable=PropertyMock, return_value=True), \
         patch.object(mgr.router, "get_active_route_sync", return_value=discovered_route), \
         patch.object(QuadernoClient, "authenticate_sync", return_value=True) as mock_auth:

        client = await mgr.get_client()
        assert client is not None
        assert client.host == "192.168.1.77"
        assert mgr._current_route == discovered_route
        mock_auth.assert_called_once()


def test_setup_logging(tmp_path):
    """Verify setup_logging configures file handler and log formatting."""
    import logging
    from quaderno_companion.config import setup_logging, settings

    test_log = tmp_path / "test_companion.log"
    with patch.object(settings, "config_dir", tmp_path), \
         patch.object(type(settings), "log_file", new_callable=PropertyMock, return_value=test_log):
        
        setup_logging(log_level="DEBUG", enable_file_logging=True, enable_console_logging=False)
        test_logger = logging.getLogger("quaderno_companion.unit_test")
        test_logger.info("Unit test log verification")

        assert test_log.exists()
        content = test_log.read_text(encoding="utf-8")
        assert "Unit test log verification" in content
        assert "[INFO]" in content


@pytest.mark.asyncio
async def test_client_upload_document_flexible_args():
    """Verify upload_document accepts both pdf_bytes/pdf_data and remote_path/filename arguments."""
    client = QuadernoClient(host="192.168.1.100")
    client._is_authenticated = True

    mock_dp = MagicMock()
    with patch.object(client, "_ensure_dp_instance", return_value=mock_dp), \
         patch.object(client, "_safe_dp_upload", return_value="doc-abc-123"):

        # 1. Test with pdf_bytes and remote_path
        doc_id_1 = await client.upload_document(
            pdf_bytes=b"%PDF-1.4 test",
            remote_path="Document/Companion/paper.pdf",
        )
        assert doc_id_1 == "doc-abc-123"

        # 2. Test with pdf_data and remote_filename
        doc_id_2 = await client.upload_document(
            pdf_data=b"%PDF-1.4 test 2",
            remote_filename="test.pdf",
            remote_folder="Document/Folder",
        )
        assert doc_id_2 == "doc-abc-123"


@pytest.mark.asyncio
async def test_device_manager_open_document():
    """Verify open_document successfully uploads PDF and invokes display_document."""
    mgr = QuadernoDeviceManager()
    mock_client = MagicMock()
    mock_client.upload_document = AsyncMock(return_value="doc-uploaded-456")
    mock_client.display_document = AsyncMock()

    with patch.object(mgr, "get_client", return_value=mock_client):
        res = await mgr.open_document(
            pdf_bytes=b"%PDF-1.4 mock",
            filename="document.pdf",
            title="My Document",
            page=2,
        )

        assert res["status"] == "success"
        assert res["document_id"] == "doc-uploaded-456"
        assert res["title"] == "My Document"
        mock_client.upload_document.assert_called_once_with(
            pdf_bytes=b"%PDF-1.4 mock",
            remote_path="Document/Companion/document.pdf",
        )
        mock_client.display_document.assert_called_once_with(
            document_id="doc-uploaded-456",
            page=1,  # PDF length fallback is 1 without valid pypdf pages
        )


@pytest.mark.asyncio
async def test_get_toc_in_memory_cache():
    """Verify get_toc returns immediately from in-memory cache."""
    mgr = QuadernoDeviceManager()
    mgr._doc_toc_cache["doc-toc-1"] = [("Chapter 1", 1), ("Chapter 2", 15)]

    toc = await mgr.get_toc("doc-toc-1")
    assert toc == [("Chapter 1", 1), ("Chapter 2", 15)]


@pytest.mark.asyncio
async def test_get_toc_persisted_disk_cache(tmp_path):
    """Verify get_toc loads cached TOC from disk when in-memory cache is empty."""
    import json
    from quaderno_companion.config import settings

    toc_file = tmp_path / "toc_cache.json"
    toc_data = {"doc-persisted-1": [["Section 1", 1], ["Section 2", 10]]}
    toc_file.write_text(json.dumps(toc_data))

    mgr = QuadernoDeviceManager()
    mgr._doc_toc_cache.clear()

    with patch.object(type(settings), "toc_cache_path", new_callable=PropertyMock, return_value=toc_file):
        toc = await mgr.get_toc("doc-persisted-1")
        assert toc == [("Section 1", 1), ("Section 2", 10)]
        assert "doc-persisted-1" in mgr._doc_toc_cache


@pytest.mark.asyncio
async def test_get_toc_fetches_from_device_and_caches(tmp_path):
    """Verify get_toc downloads document from device, parses TOC, and writes to disk cache."""
    from quaderno_companion.config import settings

    toc_file = tmp_path / "toc_cache.json"

    mgr = QuadernoDeviceManager()
    mgr._doc_toc_cache.clear()

    mock_client = MagicMock()
    mock_client.download_document_async = AsyncMock(return_value=b"%PDF-1.4 mock")

    with patch.object(type(settings), "toc_cache_path", new_callable=PropertyMock, return_value=toc_file), \
         patch.object(mgr, "get_client", return_value=mock_client), \
         patch("quaderno_companion.device.manager.extract_pdf_toc", return_value=[("Introduction", 1), ("Summary", 20)]):

        toc = await mgr.get_toc("doc-remote-1")
        assert toc == [("Introduction", 1), ("Summary", 20)]
        assert mgr._doc_toc_cache.get("doc-remote-1") == [("Introduction", 1), ("Summary", 20)]
        assert toc_file.exists()


def test_extract_pdf_toc():
    """Verify extract_pdf_toc correctly extracts outlines from PDF bytes."""
    import pymupdf as fitz
    from quaderno_companion.device.manager import extract_pdf_toc

    # 1. Test empty bytes
    assert extract_pdf_toc(b"") == []
    assert extract_pdf_toc(b"not a pdf") == []

    # 2. Create in-memory PDF with TOC bookmarks using PyMuPDF
    doc = fitz.open()
    doc.new_page()  # Page 1
    doc.new_page()  # Page 2
    doc.new_page()  # Page 3

    # TOC format in PyMuPDF: [ [lvl, title, page_1_indexed, ...] ]
    toc_in = [
        [1, "Chapter 1: Intro", 1],
        [1, "Chapter 2: Methods", 2],
        [2, "Subsection 2.1", 3],
    ]
    doc.set_toc(toc_in)
    pdf_bytes = doc.tobytes()
    doc.close()

    toc_out = extract_pdf_toc(pdf_bytes)
    assert len(toc_out) == 3
    assert toc_out[0] == ("Chapter 1: Intro", 1)
    assert toc_out[1] == ("Chapter 2: Methods", 2)
    assert toc_out[2] == ("Subsection 2.1", 3)

