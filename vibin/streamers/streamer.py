from abc import ABCMeta, abstractmethod
from enum import Enum
import typing

import upnpclient

from vibin.mediasources import MediaSource
from vibin.types_foo import ServiceSubscriptions

# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-AVTransport-v3-Service.pdf


class TransportState(Enum):
    UNKNOWN = "UNKNOWN"
    PLAYING = "PLAYING"
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"
    TRANSITIONING = "TRANSITIONING"


# Float: 0.0 -> 1.0 (for beginning -> end of track; 0.5 is half way into track)
# Int: Number of seconds into the track
# Str: h:mm:ss into the track
SeekTarget = typing.Union[float, int, str]


class Streamer(metaclass=ABCMeta):
    navigator_name = "vibin"
    model_name = "VibinStreamer"

    @abstractmethod
    def __init__(
            self,
            device: upnpclient.Device,
            subscribe_callback_base: typing.Optional[str],
            updates_handler=None,
            on_playlist_modified=None,
    ):
        pass

    @abstractmethod
    def register_media_source(self, media_source: MediaSource):
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
    def subscriptions(self) -> ServiceSubscriptions:
        pass

    @abstractmethod
    def power_toggle(self):
        pass

    @abstractmethod
    def on_event(self, service_name: str, event: str):
        pass

    @abstractmethod
    def playlist(self, call_handler_on_sync_loss=True):
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
    def repeat(self, enabled: typing.Optional[str]):
        pass

    @abstractmethod
    def shuffle(self, enabled: typing.Optional[str]):
        pass

    # TODO: Make this a settable property
    @abstractmethod
    def ignore_playlist_updates(self, ignore = False):
        pass

    # TODO: Fix the name as it's not always going to result in playing
    #   something. e.g. "APPEND" won't change what's playing.
    @abstractmethod
    def play_metadata(
            self,
            metadata: str,
            action: str = "REPLACE",
            insert_index: typing.Optional[int] = None
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
    def transport_state(self) -> TransportState:
        pass

    @abstractmethod
    def transport_status(self) -> str:
        pass

    @abstractmethod
    def subscribe(self):
        pass

    @abstractmethod
    def system_state(self):
        pass

    @abstractmethod
    def state_vars(self):
        pass

    @abstractmethod
    def vibin_vars(self):
        pass

    @abstractmethod
    def play_state(self):
        pass

    @property
    @abstractmethod
    def presets(self):
        pass

    @abstractmethod
    def play_preset_id(self, preset_id: int):
        pass
