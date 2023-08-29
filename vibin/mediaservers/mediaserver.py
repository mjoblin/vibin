from abc import ABCMeta, abstractmethod

import upnpclient

from vibin.models import (
    Album,
    Artist,
    MediaBrowseSingleLevel,
    MediaFolder,
    MediaServerState,
    Track,
    UPnPServiceSubscriptions,
)
from vibin.types import MediaId, UpdateMessageHandler, UPnPProperties


# -----------------------------------------------------------------------------
# MediaServer interface.
#
# This interface is to be implemented by any Vibin class managing a Media
# Server.
#
# The interface is strongly influenced by the Asset implementation, which means
# it is likely a very leaky abstraction exposing many design choices of the
# Asset server product.
#
# Reference UPnP documentation:
#
# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-ContentDirectory-v4-Service.pdf
# -----------------------------------------------------------------------------

class MediaServer(metaclass=ABCMeta):
    """
    Manage a media server for Vibin.

        * `device`: The `upnp.Device` instance for the media server to be
            managed.
        * `upnp_subscription_callback_base`: The REST API base URL to use when
            subscribing to media server-related UPnP service events. Events
            will be passed to the implementation's `on_upnp_event()`.
        * `on_update`: A callback to invoke when a message is ready to be sent
            back to Vibin.
    """

    model_name = "VibinMediaSource"

    @abstractmethod
    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """The UPnP device name for the Media Server."""
        pass

    @property
    @abstractmethod
    def device(self) -> upnpclient.Device:
        """The UPnP device instance associated with the Media Server."""
        pass

    @property
    @abstractmethod
    def device_state(self) -> MediaServerState:
        """System state for the Media Server."""
        pass

    @property
    @abstractmethod
    def device_udn(self) -> str:
        """The Media Server's UPnP device UDN (Unique Device Name)."""
        pass

    @abstractmethod
    def on_shutdown(self) -> None:
        """Called when the Vibin system is shut down."""
        pass

    # -------------------------------------------------------------------------
    # System

    @abstractmethod
    def clear_caches(self) -> None:
        """Clear all media caches."""
        pass

    @property
    @abstractmethod
    def url_prefix(self) -> str:
        """URL prefix to access content on the Media Server (e.g. art)."""
        pass

    # -------------------------------------------------------------------------
    # Settings

    @property
    @abstractmethod
    def all_albums_path(self) -> str | None:
        """The path on the Media Server to find all Albums.

        The path is expected to contain a flat list of all Albums with no
        nesting or sub-folders.
        """
        pass

    @all_albums_path.setter
    @abstractmethod
    def all_albums_path(self, path: str) -> None:
        pass

    @property
    @abstractmethod
    def new_albums_path(self) -> str | None:
        """The path on the Media Server to find new Albums.

        The path is expected to contain a flat list of all new Albums with no
        nesting or sub-folders.
        """
        pass

    @new_albums_path.setter
    @abstractmethod
    def new_albums_path(self, path: str) -> None:
        pass

    @property
    @abstractmethod
    def all_artists_path(self) -> str | None:
        """The path on the Media Server to find all Artists.

        The path is expected to contain a flat list of all Artists with no
        nesting or sub-folders.
        """
        pass

    @all_artists_path.setter
    @abstractmethod
    def all_artists_path(self, path: str) -> None:
        pass

    # -------------------------------------------------------------------------
    # Media

    @property
    @abstractmethod
    def albums(self) -> list[Album]:
        """Get details on all Albums on the Media Server."""
        pass

    @property
    @abstractmethod
    def new_albums(self) -> list[Album]:
        """Get details on all new Albums on the Media Server."""
        pass

    @abstractmethod
    def album_tracks(self, album_id: MediaId) -> list[Track]:
        """Get details on all Tracks for al Album on the Media Server."""
        pass

    @property
    @abstractmethod
    def artists(self) -> list[Artist]:
        """Get details on all Artists on the Media Server."""
        pass

    @abstractmethod
    def artist(self, artist_id: MediaId) -> Artist:
        """Get details on an Artist by MediaId."""
        pass

    @property
    @abstractmethod
    def tracks(self) -> list[Track]:
        """Get details on all Tracks on the Media Server."""
        pass

    @abstractmethod
    def album(self, album_id: MediaId) -> Album:
        """Get details on an Album by MediaId."""
        pass

    @abstractmethod
    def track(self, track_id: MediaId) -> Track:
        """Get details on a Track by MediaId."""
        pass

    # -------------------------------------------------------------------------
    # Browsing

    @abstractmethod
    def get_path_contents(
        self, path: str
    ) -> list[MediaFolder | Artist | Album | Track] | Track | None:
        """Retrieve the contents of the given path on the Media Server."""
        pass

    @abstractmethod
    def children(self, parent_id: MediaId = "0") -> MediaBrowseSingleLevel:
        """Retrieve information on all children of the given `parent_id`."""
        pass

    @abstractmethod
    def get_metadata(self, id: MediaId):
        """Get Media Server metadata on an item by MediaId."""
        pass

    # -------------------------------------------------------------------------
    # UPnP

    @abstractmethod
    def subscribe_to_upnp_events(self) -> None:
        """Invoked when the Media Server should initiate any UPnP service event
        subscriptions."""
        pass

    @abstractmethod
    def upnp_properties(self) -> UPnPProperties:
        """All UPnP properties for the Media Server.

        Properties are only available for any UPnP service subscriptions
        managed by the MediaServer implementation.
        """
        pass

    @property
    @abstractmethod
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        """All active UPnP subscriptions."""
        pass

    @abstractmethod
    def on_upnp_event(self, service_name: str, event: str):
        """Invoked when a UPnP event has been received from a subscription
        managed by the Media Server."""
        pass
