from abc import ABCMeta, abstractmethod
import typing

from vibin.models import Album, Track

# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-ContentDirectory-v4-Service.pdf


class MediaSource(metaclass=ABCMeta):
    model_name = "VibinMediaSource"

    @property
    @abstractmethod
    def name(self):
        pass

    @property
    @abstractmethod
    def udn(self):
        pass

    @abstractmethod
    def get_path_contents(self, path):
        pass

    @property
    @abstractmethod
    def albums(self) -> typing.List[Album]:
        pass

    @property
    @abstractmethod
    def new_albums(self) -> typing.List[Album]:
        pass

    @abstractmethod
    def album_tracks(self, album_id) -> typing.List[Track]:
        pass

    @property
    @abstractmethod
    def tracks(self) -> typing.List[Track]:
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
