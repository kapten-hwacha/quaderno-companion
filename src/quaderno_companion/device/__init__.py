"""Quaderno device communication, auto-routing, and state management."""

from quaderno_companion.device.client import (
    DeviceNotConnectedError,
    DeviceNotPairedError,
    QuadernoClient,
)
from quaderno_companion.device.manager import (
    DeviceStatus,
    NavAction,
    QuadernoDeviceManager,
    ReadingState,
    device_manager,
)
from quaderno_companion.device.router import ConnectionType, DeviceRoute, NetworkRouter

__all__ = [
    "DeviceNotConnectedError",
    "DeviceNotPairedError",
    "QuadernoClient",
    "DeviceStatus",
    "NavAction",
    "QuadernoDeviceManager",
    "ReadingState",
    "device_manager",
    "ConnectionType",
    "DeviceRoute",
    "NetworkRouter",
]
