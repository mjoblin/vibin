"""Async UPnP device discovery for vibin.

Provides async device discovery using async_upnp_client's SSDP implementation.
"""

import asyncio
from typing import Callable

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.const import DeviceOrServiceType, SsdpSource
from async_upnp_client.ssdp_listener import SsdpDevice, SsdpListener

from vibin.logger import logger
from vibin.upnp.device import VibinDevice, wrap_device


async def async_discover_devices(
    timeout: int = 5,
    device_filter: Callable[[VibinDevice], bool] | None = None,
) -> list[VibinDevice]:
    """Discover UPnP devices on the network.

    Performs an SSDP search and creates VibinDevice objects for each
    discovered device.

    Args:
        timeout: How long to wait for device responses (in seconds).
        device_filter: Optional filter function. If provided, only devices
            for which this function returns True will be included.

    Returns:
        List of discovered VibinDevice objects.

    Example:
        # Discover all devices
        devices = await async_discover_devices()

        # Discover only Cambridge Audio MediaRenderers
        devices = await async_discover_devices(
            device_filter=lambda d: (
                d.manufacturer == "Cambridge Audio" and
                "MediaRenderer" in d.device_type
            )
        )
    """
    discovered_locations: set[str] = set()

    def on_device_found(
        ssdp_device: SsdpDevice,
        device_or_service_type: DeviceOrServiceType,
        source: SsdpSource,
    ) -> None:
        """Callback for when a device is discovered."""
        location = ssdp_device.location

        if location:
            discovered_locations.add(location)

    # Create and start the SSDP listener
    listener = SsdpListener(callback=on_device_found)
    await listener.async_start()

    # Send search packet
    await listener.async_search()

    # Wait for responses
    await asyncio.sleep(timeout)

    # Stop the listener
    await listener.async_stop()

    logger.info(f"SSDP discovery found {len(discovered_locations)} device locations")

    # Create VibinDevice objects from discovered locations
    # Use a fresh session for each discovery to avoid event loop issues
    devices: list[VibinDevice] = []

    async with aiohttp.ClientSession() as session:
        requester = AiohttpSessionRequester(session, with_sleep=True)
        factory = UpnpFactory(requester, non_strict=True)

        for location in discovered_locations:
            try:
                device = await factory.async_create_device(location)
                wrapped = wrap_device(device)

                # Apply filter if provided
                if device_filter is None or device_filter(wrapped):
                    devices.append(wrapped)
                    logger.info(
                        f"Found: {wrapped.model_name} ('{wrapped.friendly_name}') "
                        f"from {wrapped.manufacturer}"
                    )
            except Exception as e:
                logger.warning(f"Failed to create device from {location}: {e}")
                # Skip devices that fail to load (network issues, invalid XML, etc.)

    return devices
