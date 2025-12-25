"""Async device factory for vibin.

Provides an async factory for creating UPnP devices using async_upnp_client.
"""

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client_factory import UpnpFactory

from vibin.upnp.device import VibinDevice, wrap_device


class VibinDeviceFactory:
    """Factory for creating UPnP devices from description URLs.

    This factory uses async_upnp_client under the hood and provides
    async device creation. It manages an aiohttp session and can be
    used as a singleton via get_instance().

    Usage:
        # Option 1: Singleton pattern (recommended)
        factory = VibinDeviceFactory.get_instance()
        await factory.async_init()
        device = await factory.async_create_device(url)

        # Option 2: With external session
        async with aiohttp.ClientSession() as session:
            factory = VibinDeviceFactory()
            await factory.async_init(session)
            device = await factory.async_create_device(url)
    """

    _instance: "VibinDeviceFactory | None" = None

    def __init__(self):
        """Initialize the factory.

        The factory is not ready to use until async_init() is called.
        """
        self._session: aiohttp.ClientSession | None = None
        self._owns_session: bool = False
        self._factory: UpnpFactory | None = None

    @classmethod
    def get_instance(cls) -> "VibinDeviceFactory":
        """Get the singleton factory instance.

        Returns:
            The shared VibinDeviceFactory instance.
        """
        if cls._instance is None:
            cls._instance = cls()

        return cls._instance

    async def async_init(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the factory with an aiohttp session.

        If no session is provided, a new session is created and will be
        managed (closed) by this factory.

        Args:
            session: Optional aiohttp ClientSession to use. If not provided,
                a new session will be created.
        """
        if self._factory is not None:
            return  # Already initialized

        if session is not None:
            self._session = session
            self._owns_session = False
        else:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        requester = AiohttpSessionRequester(self._session, with_sleep=True)
        self._factory = UpnpFactory(requester, non_strict=True)

    async def async_create_device(self, description_url: str) -> VibinDevice:
        """Create a device from its UPnP description URL.

        Args:
            description_url: The URL to the device's XML description document.

        Returns:
            A VibinDevice-compatible device object.

        Raises:
            RuntimeError: If async_init() has not been called.
            Various async_upnp_client exceptions for network/parsing errors.
        """
        if self._factory is None:
            await self.async_init()

        device = await self._factory.async_create_device(description_url)

        return wrap_device(device)

    async def async_close(self) -> None:
        """Clean up resources.

        Closes the aiohttp session if it was created by this factory.
        """
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

        self._factory = None

    @property
    def is_initialized(self) -> bool:
        """Check if the factory has been initialized."""
        return self._factory is not None
