from abc import ABCMeta, abstractmethod
from typing import Optional

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
            artist: Optional[str] = None,
            album: Optional[str] = None,
            track: Optional[str] = None,
            link_type: Optional[str] = "All",
    ) -> list[ExternalServiceLink]:
        pass
