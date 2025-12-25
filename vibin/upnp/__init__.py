"""UPnP abstraction layer for vibin.

This module provides a unified interface for UPnP device interaction
using the async_upnp_client library.
"""

from vibin.upnp.device import VibinDevice, AsyncUpnpDeviceAdapter, wrap_device
from vibin.upnp.discovery import (
    async_discover_cambridge_audio_streamer,
    async_discover_device_by_location,
    async_discover_device_by_name,
    async_discover_devices,
    async_discover_media_server,
)
from vibin.upnp.exceptions import VibinSoapError, VibinUpnpError
from vibin.upnp.factory import VibinDeviceFactory


__all__ = [
    # Device protocol and adapter
    "AsyncUpnpDeviceAdapter",
    "VibinDevice",
    "wrap_device",
    # Exceptions
    "VibinSoapError",
    "VibinUpnpError",
    # Factory
    "VibinDeviceFactory",
    # Discovery
    "async_discover_cambridge_audio_streamer",
    "async_discover_device_by_location",
    "async_discover_device_by_name",
    "async_discover_devices",
    "async_discover_media_server",
]
