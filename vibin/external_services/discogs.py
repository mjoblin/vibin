import discogs_client

from vibin.external_services import ExternalService
from vibin.models import ExternalServiceLink


class Discogs(ExternalService):
    """External service handler for Discogs.

    https://www.discogs.com
    """

    service_name = "Discogs"

    def __init__(self, user_agent: str, token: str | None):
        self._user_agent = user_agent
        self._token = token

        # TODO: Can the url base can be extracted from the client object
        self._url_base = "https://www.discogs.com"

        self._client = discogs_client.Client(user_agent=user_agent, user_token=token)

    @property
    def name(self) -> str:
        return self.service_name

    @property
    def token(self):
        return self._token

    def links(
        self,
        artist: str | None = None,
        album: str | None = None,
        track: str | None = None,
        link_type: str = "All",
    ) -> list[ExternalServiceLink]:
        links = []

        def add_link(link_type: str):
            if link_type == "Artist":
                query = artist
                kwargs = {"type": "artist"}
            elif link_type == "Album":
                query = album
                kwargs = {"artist": artist, "type": "master"}

            try:
                links.append(
                    ExternalServiceLink(
                        type=link_type,
                        name=link_type,
                        url=f"{self._url_base}{self._client.search(query, **kwargs).page(0)[0].url}",
                    )
                )
            except IndexError:
                pass

        # Discogs doesn't have track-specific links for album tracks.
        # TODO: Consider including singles releases.

        if artist and (link_type == "Artist" or link_type == "All"):
            add_link("Artist")

        if album and (link_type == "Album" or link_type == "All"):
            add_link("Album")

        return links
