"""Device abstraction for UPnP libraries.

Provides a unified interface (VibinDevice protocol) for UPnP devices.
The AsyncUpnpDeviceAdapter wraps async_upnp_client.UpnpDevice to conform
to the VibinDevice protocol.
"""

from typing import Protocol, runtime_checkable, Any


@runtime_checkable
class VibinDevice(Protocol):
    """Protocol defining the device interface vibin needs.

    Use AsyncUpnpDeviceAdapter to wrap async_upnp_client.UpnpDevice.
    """

    @property
    def friendly_name(self) -> str:
        """Human-readable name of the device."""
        ...

    @property
    def udn(self) -> str:
        """Unique Device Name (UDN) identifier."""
        ...

    @property
    def device_type(self) -> str:
        """UPnP device type URN."""
        ...

    @property
    def manufacturer(self) -> str:
        """Device manufacturer name."""
        ...

    @property
    def model_name(self) -> str:
        """Device model name."""
        ...

    @property
    def location(self) -> str:
        """URL to the device's XML description."""
        ...


class AsyncUpnpDeviceAdapter:
    """Adapts async_upnp_client.UpnpDevice to the VibinDevice protocol.

    The main difference is that async_upnp_client uses `device_url` instead
    of `location` for the device description URL.
    """

    def __init__(self, device: Any):
        """Initialize the adapter.

        Args:
            device: An async_upnp_client.UpnpDevice instance.
        """
        self._device = device

    @property
    def friendly_name(self) -> str:
        """Human-readable name of the device."""
        return self._device.friendly_name

    @property
    def udn(self) -> str:
        """Unique Device Name (UDN) identifier."""
        return self._device.udn

    @property
    def device_type(self) -> str:
        """UPnP device type URN."""
        return self._device.device_type

    @property
    def manufacturer(self) -> str:
        """Device manufacturer name."""
        return self._device.manufacturer

    @property
    def model_name(self) -> str:
        """Device model name."""
        return self._device.model_name

    @property
    def location(self) -> str:
        """URL to the device's XML description.

        Maps async_upnp_client's device_url to the location property.
        """
        return self._device.device_url

    @property
    def wrapped_device(self) -> Any:
        """Access the underlying async_upnp_client.UpnpDevice.

        Useful for accessing async_upnp_client-specific functionality
        like services and async actions.
        """
        return self._device


def wrap_device(device: Any) -> VibinDevice:
    """Wrap a UPnP device to conform to VibinDevice protocol.

    Args:
        device: An async_upnp_client.UpnpDevice instance.

    Returns:
        A VibinDevice-compatible object (AsyncUpnpDeviceAdapter).
    """
    # async_upnp_client uses device_url instead of location
    if hasattr(device, "device_url"):
        return AsyncUpnpDeviceAdapter(device)

    # Already conforms to VibinDevice protocol
    return device
