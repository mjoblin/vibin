from abc import ABCMeta, abstractmethod
from typing import Callable

import upnpclient

from vibin.models import (
    AmplifierState,
    AudioSource,
    AudioSources,
    UPnPServiceSubscriptions,
)
from vibin.types import (
    MuteState,
    PowerState,
    UpdateMessageHandler,
    UPnPProperties,
    AmplifierAction,
)


# -----------------------------------------------------------------------------
# Amplifier interface.
#
# This interface is to be implemented by any Vibin class managing an amplifier.
#
# The interface is strongly influenced by the Hegel implementation, which means
# it is likely a very leaky abstraction exposing many design choices of the
# Hegel server product.
# -----------------------------------------------------------------------------


class Amplifier(metaclass=ABCMeta):
    """
    Manage an amplifier for Vibin.

        * `device`: The `upnp.Device` instance for the media server to be
            managed.
        * `upnp_subscription_callback_base`: The REST API base URL to use when
            subscribing to media server-related UPnP service events. Events
            will be passed to the implementation's `on_upnp_event()`.
        * `on_update`: A callback to invoke when a message is ready to be sent
            back to Vibin.
    """

    model_name = "VibinAmplifier"

    @abstractmethod
    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """The UPnP device name for the Amplifier."""
        pass

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether an active connection has been established."""
        pass

    @property
    @abstractmethod
    def device(self) -> upnpclient.Device:
        """The UPnP device instance associated with the Amplifier."""
        pass

    @property
    @abstractmethod
    def device_state(self) -> AmplifierState:
        """System state for the Amplifier."""
        pass

    @property
    @abstractmethod
    def device_udn(self) -> str:
        """The Amplifier's UPnP device UDN (Unique Device Name)."""
        pass

    @abstractmethod
    def on_startup(self) -> None:
        """Called when the Vibin system has started up."""
        pass

    @abstractmethod
    def on_shutdown(self) -> None:
        """Called when the Vibin system is shut down."""
        pass

    # -------------------------------------------------------------------------
    # System

    @property
    @abstractmethod
    def actions(self) -> list[AmplifierAction]:
        """Actions supported by this amplifier."""
        pass

    @property
    @abstractmethod
    def power(self) -> PowerState | None:
        """Power state, if known."""
        pass

    @power.setter
    @abstractmethod
    def power(self, state: PowerState) -> None:
        """Set the power state. No-op if not supported."""
        pass

    @abstractmethod
    def power_toggle(self) -> None:
        """Toggle the power state. No-op if not supported."""
        pass

    @property
    @abstractmethod
    def volume(self) -> float | None:
        """Current volume (0-1), if known."""
        pass

    @volume.setter
    @abstractmethod
    def volume(self, volume: float) -> None:
        """Set the volume (0-1). No-op if not supported."""
        pass

    @abstractmethod
    def volume_up(self) -> None:
        """Increase the volume by one unit. No-op if not supported."""
        pass

    @abstractmethod
    def volume_down(self) -> None:
        """Decrease the volume by one unit. No-op if not supported."""
        pass

    @property
    @abstractmethod
    def mute(self) -> MuteState | None:
        """Mute state, if known."""
        pass

    @mute.setter
    @abstractmethod
    def mute(self, state: MuteState) -> None:
        """Set the mute state. No-op if not supported."""
        pass

    @abstractmethod
    def mute_toggle(self) -> None:
        """Toggle the mute state. No-op if not supported."""
        pass

    @property
    @abstractmethod
    def audio_sources(self) -> AudioSources | None:
        """Get the Audio Sources, if any."""
        pass

    @property
    @abstractmethod
    def audio_source(self) -> AudioSource | None:
        """Get the active Audio Source, if known."""
        pass

    @audio_source.setter
    @abstractmethod
    def audio_source(self, source: str) -> None:
        """Set the active Audio Source by name. No-op if not supported."""
        pass

    # -------------------------------------------------------------------------
    # UPnP
    #
    # These methods have default "do nothing" implementations, so concrete
    # classes can choose to ignore them if they're not relevant.

    @abstractmethod
    def subscribe_to_upnp_events(self) -> None:
        """Invoked when the Amplifier should initiate any UPnP service event
        subscriptions."""
        return None

    @abstractmethod
    def upnp_properties(self) -> UPnPProperties:
        """All UPnP properties for the Amplifier.

        Properties are only available for any UPnP service subscriptions
        managed by the Amplifier implementation.
        """
        return {}

    @property
    @abstractmethod
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        """All active UPnP subscriptions."""
        return {}

    @abstractmethod
    def on_upnp_event(self, service_name: str, event: str):
        """Invoked when a UPnP event has been received from a subscription
        managed by the Amplifier."""
        return None
