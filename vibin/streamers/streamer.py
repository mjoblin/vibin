from abc import ABCMeta, abstractmethod
from enum import Enum
import typing

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
    def on_event(self, service_name: str, event: str):
        pass

    @abstractmethod
    def playlist(self):
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
    def repeat(self, enabled: typing.Optional[bool]):
        pass

    @abstractmethod
    def shuffle(self, enabled: typing.Optional[bool]):
        pass

    @abstractmethod
    def play_metadata(self, metadata: str):
        pass

    @abstractmethod
    def play_playlist_index(self, index: int):
        pass

    @abstractmethod
    def play_playlist_id(self, playlist_id: int):
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
    def state_vars(self):
        pass

    @abstractmethod
    def vibin_vars(self):
        pass

    @abstractmethod
    def play_state(self):
        pass
