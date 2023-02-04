from abc import ABCMeta, abstractmethod

from vibin.models import ExternalServiceLink


class ExternalService(metaclass=ABCMeta):
    @abstractmethod
    def __init__(self, user_agent: str, token: str):
        pass

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def links(
            self,
            artist: str | None = None,
            album: str | None = None,
            track: str | None = None,
            link_type: str = "All",
    ) -> list[ExternalServiceLink]:
        pass
