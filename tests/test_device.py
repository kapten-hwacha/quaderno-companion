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
    
    # Mock probe to succeed on the 2nd candidate (bluetooth_pan)
    async def mock_probe(host, port, timeout=1.0):
        return host == "192.168.128.1"

    with patch.object(router, "_probe_endpoint", side_effect=mock_probe):
        route = await router.get_active_route(force_refresh=True)
        assert route is not None
        assert route.connection_type == "bluetooth_pan"
        assert route.host == "192.168.128.1"


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
