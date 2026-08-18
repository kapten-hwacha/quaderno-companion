"""Network Auto-Routing and Interface Probe for Fujitsu Quaderno Gen 2.

Handles automatic discovery and failover across:
1. Wi-Fi (Configured static IP, dynamic gateway, or mDNS hostname `digitalpaper.local`)
2. Wi-Fi Access Point / SoftAP (`192.168.43.1` or default gateway)
3. Bluetooth PAN (Network interface gateway `192.168.128.1` / `bnep0` / `en*`)
4. USB Tethering (`172.25.47.1`)
"""

import asyncio
import logging
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional, Set, Tuple

from quaderno_companion.config import settings

logger = logging.getLogger(__name__)

ConnectionType = Literal["wifi", "wifi_ap", "bluetooth_pan", "usb", "unknown"]


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

            # If standard candidates fail, try a quick subnet probe
            subnet_candidate = await self._probe_local_subnet()
            if subnet_candidate:
                subnet_candidate.is_reachable = True
                self._cached_route = subnet_candidate
                logger.info(
                    f"Discovered Quaderno via subnet scan ({subnet_candidate.connection_type.upper()}) "
                    f"at {subnet_candidate.host}:{subnet_candidate.port}"
                )
                return subnet_candidate

            logger.warning("No active network route to Quaderno found across Wi-Fi, Wi-Fi AP, Bluetooth PAN, or USB.")
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

        logger.warning("No active network route to Quaderno found across Wi-Fi, Wi-Fi AP, Bluetooth PAN, or USB.")
        return None

    def invalidate_cache(self) -> None:
        """Mark cached route as stale."""
        self._cached_route = None

    def _get_default_gateways(self) -> List[str]:
        """Retrieve default gateway IPs from system routing table across Linux and macOS."""
        gateways: List[str] = []
        seen: Set[str] = set()

        def _add(gw: str):
            clean = gw.strip()
            if clean and clean != "127.0.0.1" and re.match(r"^\d+\.\d+\.\d+\.\d+$", clean) and clean not in seen:
                seen.add(clean)
                gateways.append(clean)

        # Method 1: Linux `ip -4 route show default` or `ip route`
        try:
            out = subprocess.check_output(
                ["ip", "-4", "route", "show", "default"],
                text=True,
                timeout=1.0,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                if "via" in parts:
                    idx = parts.index("via")
                    if idx + 1 < len(parts):
                        _add(parts[idx + 1])
        except Exception:
            pass

        # Method 2: Linux /proc/net/route table (when tools like `ip` are missing)
        try:
            route_path = Path("/proc/net/route")
            if route_path.exists():
                for line in route_path.read_text(encoding="utf-8").splitlines()[1:]:
                    fields = line.strip().split()
                    if len(fields) >= 3 and fields[1] == "00000000":  # Destination default
                        gw_hex = fields[2]
                        if len(gw_hex) == 8 and gw_hex != "00000000":
                            # Little-endian hex to dotted decimal
                            gw_ip = ".".join(str(int(gw_hex[i:i+2], 16)) for i in (6, 4, 2, 0))
                            _add(gw_ip)
        except Exception:
            pass

        # Method 3: macOS / BSD `netstat -nr -f inet`
        try:
            out = subprocess.check_output(
                ["netstat", "-nr", "-f", "inet"],
                text=True,
                timeout=1.0,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "default":
                    _add(parts[1])
        except Exception:
            pass

        return gateways

    def _build_candidate_list(self) -> List[DeviceRoute]:
        """Construct prioritized list of candidate endpoints."""
        candidates = []
        seen_hosts: Set[str] = set()

        def add_candidate(host: Optional[str], conn_type: ConnectionType):
            if host and host not in seen_hosts:
                seen_hosts.add(host)
                candidates.append(
                    DeviceRoute(
                        host=host,
                        port=settings.device_port,
                        connection_type=conn_type,
                    )
                )

        # 1. Configured static IP (if provided)
        add_candidate(settings.device_ip, "wifi")

        # 2. Dynamic Default Gateway (When connected directly to Quaderno AP or hotspot)
        for gw in self._get_default_gateways():
            add_candidate(gw, "wifi_ap")

        # 3. Standard Android SoftAP / Wi-Fi Access Point gateway
        if hasattr(settings, "device_ap_ip") and settings.device_ap_ip:
            add_candidate(settings.device_ap_ip, "wifi_ap")

        # 4. mDNS hostname on Wi-Fi
        add_candidate(settings.device_wifi_host, "wifi")

        # 5. Bluetooth PAN interface / gateway
        add_candidate(settings.device_bluetooth_gateway, "bluetooth_pan")

        # 6. USB Interface
        add_candidate(settings.device_usb_ip, "usb")

        return candidates

    async def _probe_local_subnet(self) -> Optional[DeviceRoute]:
        """Perform a rapid parallel probe across the local /24 subnet on device_port."""
        try:
            # Determine local IP by creating a dummy UDP socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            parts = local_ip.split(".")
            if len(parts) != 4 or not (local_ip.startswith("192.168.") or local_ip.startswith("10.") or local_ip.startswith("172.")):
                return None

            subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."

            async def probe_ip(ip: str) -> Optional[str]:
                if ip == local_ip:
                    return None
                if await self._probe_endpoint(ip, settings.device_port, timeout=0.35):
                    return ip
                return None

            tasks = [probe_ip(f"{subnet_prefix}{i}") for i in range(1, 255)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, str):
                    return DeviceRoute(
                        host=res,
                        port=settings.device_port,
                        connection_type="wifi",
                    )
        except Exception:
            pass
        return None

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
