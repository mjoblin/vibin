"""UPnP abstraction layer for vibin.

This module provides a unified interface for UPnP device interaction
using the async_upnp_client library.
"""

from vibin.upnp.device import VibinDevice, AsyncUpnpDeviceAdapter, wrap_device
from vibin.upnp.exceptions import VibinUpnpError, VibinSoapError
from vibin.upnp.factory import VibinDeviceFactory
from vibin.upnp.discovery import (
    async_discover_devices,
    async_discover_device_by_location,
    async_discover_cambridge_audio_streamer,
    async_discover_media_server,
    async_discover_device_by_name,
)

__all__ = [
    # Device protocol and adapter
    "VibinDevice",
    "AsyncUpnpDeviceAdapter",
    "wrap_device",
    # Exceptions
    "VibinUpnpError",
    "VibinSoapError",
    # Factory
    "VibinDeviceFactory",
    # Discovery
    "async_discover_devices",
    "async_discover_device_by_location",
    "async_discover_cambridge_audio_streamer",
    "async_discover_media_server",
    "async_discover_device_by_name",
]
