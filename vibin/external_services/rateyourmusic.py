from functools import lru_cache
from typing import Optional

from vibin import VibinError
from vibin.external_services import ExternalService
from vibin.models import ExternalServiceLink


class RateYourMusic(ExternalService):
    service_name = "RateYourMusic"

    def __init__(self, user_agent: str, token: str):
        self._user_agent = user_agent
        self._token = token

        self._url_base = "https://rateyourmusic.com"

    @property
    def name(self) -> str:
        return self.service_name

    def _rym_friendly_path(self, path: str) -> str:
        return path.lower().replace(" ", "-")

    @lru_cache
    def links(
            self,
            artist: Optional[str] = None,
            album: Optional[str] = None,
            track: Optional[str] = None,
            link_type: str = "All",
    ) -> list[ExternalServiceLink]:
        links = []

        # TODO: These links are not validated as RYM will detect them as not
        #   coming from a browser and will block the IP.
        #
        # Ideally, in the future RYM will support API access. See:
        # https://rateyourmusic.com/development/
        #
        # The https://github.com/dbeley/rymscraper project uses Selenium to
        # scrape RYM.

        if link_type == "All" or artist:
            url = f"{self._url_base}/artist/{self._rym_friendly_path(artist)}"

            links.append(ExternalServiceLink(
                type="Artist",
                name="Artist",
                url=url,
            ))

        if link_type == "All" or (artist and album):
            url = f"{self._url_base}/release/album/{self._rym_friendly_path(artist)}/{self._rym_friendly_path(album)}"

            links.append(ExternalServiceLink(
                type="Album",
                name="Album",
                url=url,
            ))

        return links

    @lru_cache
    def descriptors(self, artist: str, album: str):
        return []
