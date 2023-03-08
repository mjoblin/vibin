from functools import lru_cache
from typing import Optional

import wikipedia

from vibin import VibinError
from vibin.external_services import ExternalService
from vibin.models import ExternalServiceLink


class Wikipedia(ExternalService):
    service_name = "Wikipedia"

    def __init__(self, user_agent: str, token: str):
        # These are unused for Wikipedia.
        self._user_agent = user_agent
        self._token = token

    @property
    def name(self) -> str:
        return self.service_name

    @lru_cache
    def links(
            self,
            artist: Optional[str] = None,
            album: Optional[str] = None,
            track: Optional[str] = None,
            link_type: str = "All",
    ) -> list[ExternalServiceLink]:
        links = []

        def add_link(link_type: str):
            if link_type == "Artist":
                query = f"{artist} band artist"
            elif link_type == "Album":
                query = f"{album} album"
            else:
                query = f"{track} song"

            try:
                search_result = wikipedia.search(query, results=1)
                page_data = wikipedia.page(search_result[0], auto_suggest=False)

                if page_data:
                    links.append(ExternalServiceLink(
                        type=link_type,
                        name=link_type,
                        url=page_data.url,
                    ))
            except IndexError:
                pass

        if artist and (link_type == "Artist" or link_type == "All"):
            add_link("Artist")

        if album and (link_type == "Album" or link_type == "All"):
            add_link("Album")

        if track and (link_type == "Track" or link_type == "All"):
            add_link("Track")

        return links
