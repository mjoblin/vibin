import re

import lyricsgenius

from vibin import VibinError
from vibin.external_services import ExternalService
from vibin.models import ExternalServiceLink, LyricsChunk


class Genius(ExternalService):
    service_name = "Genius"

    def __init__(self, user_agent: str, token: str):
        self._user_agent = user_agent
        self._token = token

        try:
            self._client = lyricsgenius.Genius(access_token=token)
        except TypeError:
            self._client = None

    @property
    def name(self) -> str:
        return self.service_name

    def links(
        self,
        artist: str | None = None,
        album: str | None = None,
        track: str | None = None,
        link_type: str = "All",
    ) -> list[ExternalServiceLink]:
        if not self._client:
            return []

        links = []

        # TODO: Consider parallelizing these (they can take seconds each).

        if artist and (link_type == "Artist" or link_type == "All"):
            result = self._client.search_artist(artist_name=artist, max_songs=0)

            if result is not None:
                links.append(
                    ExternalServiceLink(
                        type="Artist",
                        name="Artist",
                        url=f"{result.url}",
                    )
                )

        if album and (link_type == "Album" or link_type == "All"):
            result = self._client.search_album(name=album, artist=artist)

            if result is not None:
                links.append(
                    ExternalServiceLink(
                        type="Album",
                        name="Album",
                        url=f"{result.url}",
                    )
                )

        if track and (link_type == "Track" or link_type == "All"):
            result = self._client.search_song(title=track, artist=artist)

            if result is not None:
                links.append(
                    ExternalServiceLink(
                        type="Track",
                        name="Lyrics",
                        url=f"{result.url}",
                    )
                )

        return links

    def lyrics(self, artist: str, track: str) -> list[LyricsChunk] | None:
        if not self._client:
            return None

        try:
            song = self._client.search_song(artist=artist, title=track)

            if song is None:
                return None

            # Munge the lyrics into a new shape. Currently, they're one long
            # string -- where chunks (choruses, verses, etc) are separated by
            # two newlines (usually; sometimes it's just one newline). Each
            # chunk may or may not have a header of sorts, which looks like
            # "[Header]". The goal is to create something like:
            #
            # [
            #     {
            #         "header": "Verse 1",
            #         "body": [
            #             "Line 1",
            #             "Line 2",
            #             "Line 3",
            #         ],
            #     },
            #     {
            #         "header": "Verse 2",
            #         "body": [
            #             "Line 1",
            #             "Line 2",
            #             "Line 3",
            #         ],
            #     },
            # ]

            # TODO: If a line matches "^\[[^\[\]]+\]$" then start a new chunk

            # The lyrics scraper allows some strings through which are not part
            # of the lyrics for a song. This includes "You might also like"
            # which could be anywhere, as well as "<digits>Embed" at the end of
            # a line. We remove those. Doing this is prone to issues; it would
            # be far better not to use a lyrics scraper.
            #
            # The lyrics also sometimes include a string like "[Chorus]" on its
            # own line. These denote a "chunk". Usually a line like "[Chorus]"
            # is preceded by two newlines, but sometimes it's not -- so we also
            # enforce at least new newlines so we can later split on multiple
            # newlines to isolate each chuck.

            lyrics = song.lyrics

            lyrics = lyrics.replace("You might also like", "")

            # Enforce at least two newlines before any line looking like
            # "[Chorus]".
            chunks_as_strings = re.split(
                r"\n{2,}", re.sub(r"(\n\[[^\[\]]+\])", r"\n\1", lyrics)
            )

            # The lyrics scraper prepends the first line of lyrics with
            # "<song title>Lyrics", so we remove that if we see it.
            chunks_as_strings[0] = re.sub(r"^.*Lyrics", "", chunks_as_strings[0])

            # The scraper also might append "<digits>Embed" to the last line.
            chunks_as_strings[-1] = re.sub(r"\d*Embed$", "", chunks_as_strings[-1])

            chunks_as_arrays = [chunk.split("\n") for chunk in chunks_as_strings]

            results = []

            for chunk in chunks_as_arrays:
                chunk_header = re.match(r"^\[([^\[\]]+)\]$", chunk[0])
                if chunk_header:
                    results.append(
                        LyricsChunk(
                            header=chunk_header.group(1),
                            body=chunk[1:],
                        )
                    )
                else:
                    results.append(
                        LyricsChunk(
                            header=None,
                            body=chunk,
                        )
                    )

            return results
        except (KeyError, IndexError) as e:
            raise VibinError(
                f"Could not extract track details for lyrics lookup for: "
                + f"{artist} - {track}: {e}"
            )

        return None
