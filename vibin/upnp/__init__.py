"""UPnP abstraction layer for vibin.

This module provides a unified interface for UPnP device interaction,
supporting both upnpclient and async_upnp_client libraries.
"""

from vibin.upnp.device import VibinDevice, AsyncUpnpDeviceAdapter, wrap_device
from vibin.upnp.exceptions import VibinUpnpError, VibinSoapError

__all__ = [
    "VibinDevice",
    "AsyncUpnpDeviceAdapter",
    "wrap_device",
    "VibinUpnpError",
    "VibinSoapError",
]
