"""Network Auto-Routing and Interface Probe for Fujitsu Quaderno Gen 2.

Handles automatic discovery and failover across:
1. Wi-Fi (Configured static IP or mDNS hostname `digitalpaper.local`)
2. Bluetooth PAN (Network interface gateway `192.168.128.1` / `bnep0` / `en*`)
3. USB Tethering (`172.25.47.1`)
"""

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)

ConnectionType = Literal["wifi", "bluetooth_pan", "usb", "unknown"]


@dataclass
class DeviceRoute:
    """Resolved active network route to the Quaderno device."""
    host: str
    port: int
    connection_type: ConnectionType
    is_reachable: bool = False


class NetworkRouter:
    """Probes candidate network routes and selects the fastest active interface."""

    def __init__(self):
        self._cached_route: Optional[DeviceRoute] = None
        self._lock = asyncio.Lock()

    async def get_active_route(self, force_refresh: bool = False) -> Optional[DeviceRoute]:
        """Discover and return the best available route to the device."""
        async with self._lock:
            if not force_refresh and self._cached_route and self._cached_route.is_reachable:
                # Fast health check on cached route
                if await self._probe_endpoint(self._cached_route.host, self._cached_route.port, timeout=0.8):
                    return self._cached_route

            # Probe candidate list in priority order
            candidates = self._build_candidate_list()
            for candidate in candidates:
                logger.debug(f"Probing route candidate: {candidate.host}:{candidate.port} ({candidate.connection_type})")
                if await self._probe_endpoint(candidate.host, candidate.port, timeout=1.2):
                    candidate.is_reachable = True
                    self._cached_route = candidate
                    logger.info(
                        f"Connected to Quaderno via {candidate.connection_type.upper()} "
                        f"at {candidate.host}:{candidate.port}"
                    )
                    return candidate

            logger.warning("No active network route to Quaderno found across Wi-Fi, Bluetooth PAN, or USB.")
            return None

    def get_active_route_sync(self, force_refresh: bool = False) -> Optional[DeviceRoute]:
        """Synchronously discover and return the best available route to the device."""
        if not force_refresh and self._cached_route and self._cached_route.is_reachable:
            if self._probe_endpoint_sync(self._cached_route.host, self._cached_route.port, timeout=0.8):
                return self._cached_route

        candidates = self._build_candidate_list()
        for candidate in candidates:
            logger.debug(f"Probing route candidate (sync): {candidate.host}:{candidate.port} ({candidate.connection_type})")
            if self._probe_endpoint_sync(candidate.host, candidate.port, timeout=1.2):
                candidate.is_reachable = True
                self._cached_route = candidate
                logger.info(
                    f"Connected to Quaderno via {candidate.connection_type.upper()} "
                    f"at {candidate.host}:{candidate.port}"
                )
                return candidate

        logger.warning("No active network route to Quaderno found across Wi-Fi, Bluetooth PAN, or USB.")
        return None

    def invalidate_cache(self) -> None:
        """Mark cached route as stale."""
        self._cached_route = None

    def _build_candidate_list(self) -> List[DeviceRoute]:
        """Construct prioritized list of candidate endpoints."""
        candidates = []

        # 1. Configured static IP (if provided)
        if settings.device_ip:
            candidates.append(
                DeviceRoute(
                    host=settings.device_ip,
                    port=settings.device_port,
                    connection_type="wifi",
                )
            )

        # 2. mDNS hostname on Wi-Fi
        if settings.device_wifi_host:
            candidates.append(
                DeviceRoute(
                    host=settings.device_wifi_host,
                    port=settings.device_port,
                    connection_type="wifi",
                )
            )

        # 3. Bluetooth PAN interface / gateway
        if settings.device_bluetooth_gateway:
            candidates.append(
                DeviceRoute(
                    host=settings.device_bluetooth_gateway,
                    port=settings.device_port,
                    connection_type="bluetooth_pan",
                )
            )

        # 4. USB Interface
        if settings.device_usb_ip:
            candidates.append(
                DeviceRoute(
                    host=settings.device_usb_ip,
                    port=settings.device_port,
                    connection_type="usb",
                )
            )

        return candidates

    def _probe_endpoint_sync(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """Synchronously probe TCP connection to host:port."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (OSError, socket.gaierror, TimeoutError):
            return False

    async def _probe_endpoint(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """Asynchronously probe TCP connection to host:port."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, OSError, socket.gaierror):
            return False
