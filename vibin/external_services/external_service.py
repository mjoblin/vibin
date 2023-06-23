from abc import ABCMeta, abstractmethod

from vibin.models import ExternalServiceLink


class ExternalService(metaclass=ABCMeta):
    @abstractmethod
    def __init__(self, user_agent: str, token: str | None):
        pass

    @property
    @abstractmethod
    def name(self):
        pass

    @property
    @abstractmethod
    def token(self):
        pass

    @abstractmethod
    def links(
        self,
        artist: str | None = None,
        album: str | None = None,
        track: str | None = None,
        link_type: str | None = "All",
    ) -> list[ExternalServiceLink]:
        pass
