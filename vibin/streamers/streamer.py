from abc import ABCMeta, abstractmethod

import upnpclient

from vibin.mediaservers import MediaServer
from vibin.models import (
    CurrentlyPlaying,
    ActivePlaylist,
    Presets,
    UPnPServiceSubscriptions,
    StreamerDeviceDisplay,
    StreamerState,
    TransportState,
    TransportPlayState,
)
from vibin.types import SeekTarget, UpdateMessageHandler, UPnPProperties

# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-AVTransport-v3-Service.pdf


# class TransportState(Enum):
#     UNKNOWN = "UNKNOWN"
#     PLAYING = "PLAYING"
#     STOPPED = "STOPPED"
#     PAUSED = "PAUSED"
#     TRANSITIONING = "TRANSITIONING"


class Streamer(metaclass=ABCMeta):
    navigator_name = "vibin"
    model_name = "VibinStreamer"

    @abstractmethod
    def __init__(
        self,
        device: upnpclient.Device,
        subscribe_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
        on_playlist_modified=None,
    ):
        pass

    @property
    @abstractmethod
    def device(self):
        pass

    @abstractmethod
    def register_media_server(self, media_server: MediaServer):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @property
    @abstractmethod
    def name(self):
        pass

    @property
    @abstractmethod
    def device_udn(self):
        pass

    @property
    @abstractmethod
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        pass

    @abstractmethod
    def power_toggle(self):
        pass

    @abstractmethod
    def set_source(self, source: str):
        pass

    @abstractmethod
    def on_upnp_event(self, service_name: str, event: str):
        pass

    @abstractmethod
    def playlist(self) -> ActivePlaylist:
        pass

    @abstractmethod
    def play(self):
        pass

    @abstractmethod
    def pause(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def seek(self, target: SeekTarget):
        pass

    @abstractmethod
    def next_track(self):
        pass

    @abstractmethod
    def previous_track(self):
        pass

    @abstractmethod
    def repeat(self, enabled: str | None):
        pass

    @abstractmethod
    def shuffle(self, enabled: str | None):
        pass

    # TODO: Make this a settable property
    @abstractmethod
    def ignore_playlist_updates(self, ignore=False):
        pass

    # TODO: Fix the name as it's not always going to result in playing
    #   something. e.g. "APPEND" won't change what's playing.
    @abstractmethod
    def play_metadata(
        self,
        metadata: str,
        action: str = "REPLACE",
        insert_index: int | None = None,
    ):
        pass

    @abstractmethod
    def play_playlist_index(self, index: int):
        pass

    @abstractmethod
    def play_playlist_id(self, playlist_id: int):
        pass

    @abstractmethod
    def playlist_clear(self):
        pass

    @abstractmethod
    def playlist_delete_entry(self, playlist_id: int):
        pass

    @abstractmethod
    def playlist_move_entry(self, playlist_id: int, from_index: int, to_index: int):
        pass

    @abstractmethod
    def transport_position(self):
        pass

    @abstractmethod
    def transport_actions(self):
        pass

    @abstractmethod
    def transport_active_controls(self):
        pass

    @abstractmethod
    def transport_state(self) -> TransportState:
        pass

    @abstractmethod
    def transport_status(self) -> str:
        pass

    @abstractmethod
    def subscribe(self):
        pass

    @property
    @abstractmethod
    def device_state(self) -> StreamerState:
        pass

    @abstractmethod
    def upnp_properties(self) -> UPnPProperties:
        pass

    @abstractmethod
    def vibin_vars(self):
        pass

    @abstractmethod
    def currently_playing(self) -> CurrentlyPlaying:
        pass

    @abstractmethod
    def play_state(self) -> TransportPlayState:
        pass

    @abstractmethod
    def device_display(self) -> StreamerDeviceDisplay:
        pass

    @property
    @abstractmethod
    def presets(self) -> Presets:
        pass

    @abstractmethod
    def play_preset_id(self, preset_id: int):
        pass
