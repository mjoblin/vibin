from abc import ABCMeta, abstractmethod

import upnpclient

from vibin.models import (
    Album,
    Artist,
    MediaServerState,
    Track,
    UPnPServiceSubscriptions,
)
from vibin.types import UpdateMessageHandler, UPnPProperties


# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-ContentDirectory-v4-Service.pdf


# TODO: Rename MediaServer
class MediaSource(metaclass=ABCMeta):
    model_name = "VibinMediaSource"

    @abstractmethod
    def __init__(
        self,
        device: upnpclient.Device,
        subscribe_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def device(self):
        pass

    @abstractmethod
    def upnp_properties(self) -> UPnPProperties:
        pass

    @property
    @abstractmethod
    def all_albums_path(self):
        pass

    @property
    @abstractmethod
    def new_albums_path(self):
        pass

    @property
    @abstractmethod
    def all_artists_path(self):
        pass

    @property
    @abstractmethod
    def url_prefix(self):
        pass

    @property
    @abstractmethod
    def system_state(self) -> MediaServerState:
        pass

    @property
    @abstractmethod
    def subscriptions(self) -> UPnPServiceSubscriptions:
        pass

    @property
    @abstractmethod
    def udn(self):
        pass

    @abstractmethod
    def clear_caches(self):
        pass

    @abstractmethod
    def get_path_contents(self, path):
        pass

    @property
    @abstractmethod
    def albums(self) -> list[Album]:
        pass

    @property
    @abstractmethod
    def new_albums(self) -> list[Album]:
        pass

    @abstractmethod
    def album_tracks(self, album_id) -> list[Track]:
        pass

    @property
    @abstractmethod
    def artists(self) -> list[Album]:
        pass

    @abstractmethod
    def artist(self, artist_id: str) -> Artist:
        pass

    @property
    @abstractmethod
    def tracks(self) -> list[Track]:
        pass

    @abstractmethod
    def album(self, album_id: str) -> Album:
        pass

    @abstractmethod
    def track(self, track_id: str) -> Track:
        pass

    @abstractmethod
    def children(self, parent_id: str = "0"):
        pass

    @abstractmethod
    def get_metadata(self, id: str):
        pass
