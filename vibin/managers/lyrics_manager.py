import re

from tinydb import Query
from tinyrecord import transaction
import xml
import xmltodict

from vibin import VibinError, VibinNotFoundError
from vibin.external_services import ExternalService
from vibin.logger import logger
from vibin.models import Lyrics
from vibin.types import MediaId
from vibin.utils import requires_external_service_token


class LyricsManager:
    """Lyrics manager.

    Manages the retrieval (from Genius) and local storage of lyrics. Also
    supports lyrics search.

    The provided db is expected to be a TinyDB table.
    """

    def __init__(self, db, genius_service: ExternalService | None = None):
        self._db = db
        self._external_service = genius_service

    @requires_external_service_token
    def lyrics_for_track(
        self,
        update_cache=False,
        *,
        track_id: MediaId | None = None,
        artist: str | None = None,
        title: str | None = None,
    ) -> Lyrics | None:
        """Return the lyrics for the given track_id or artist/title pair.

        The track_id use case is intended for local media, and the artist/title
        pair use case is intended for other non-local sources like AirPlay."""

        if track_id is None and (artist is None or title is None):
            return

        def storage_id(track_id, artist, title) -> str:
            """Return the unique database storage ID for this id/artist/title."""
            if track_id:
                return track_id

            return f"{artist}::{title}"

        # Check if lyrics are already stored
        StoredLyricsQuery = Query()
        stored_lyrics = self._db.get(
            StoredLyricsQuery.lyrics_id == storage_id(track_id, artist, title)
        )

        if stored_lyrics is not None:
            if update_cache:
                with transaction(self._db) as tr:
                    tr.remove(doc_ids=[stored_lyrics.doc_id])
            else:
                lyrics_data = Lyrics(**stored_lyrics)
                return lyrics_data

        if track_id:
            # Extract artist and title from the media metadata
            try:
                track_info = xmltodict.parse(self.media_server.get_metadata(track_id))

                artist = track_info["DIDL-Lite"]["item"]["dc:creator"]
                title = track_info["DIDL-Lite"]["item"]["dc:title"]
            except xml.parsers.expat.ExpatError as e:
                logger.error(
                    f"Could not convert XML to JSON for track: {track_id}: {e}"
                )
                return None

        try:
            # Get the lyrics for the artist/title from Genius, and persist to
            # the local store. Missing lyrics are still persisted, just as an
            # empty chunk list -- this is done to prevent always looking for
            # lyrics every time the track is played (the caller can always
            # manually request a retry by specifying update_cache=True).
            lyric_chunks = self._external_service.lyrics(artist, title)

            lyric_data = Lyrics(
                lyrics_id=storage_id(track_id, artist, title),
                media_id=track_id,
                is_valid=True,
                chunks=lyric_chunks if lyric_chunks is not None else [],
            )

            with transaction(self._db) as tr:
                tr.insert(lyric_data.dict())

            return lyric_data
        except VibinError as e:
            logger.error(e)

        return None

    def set_is_valid(self, lyrics_id: str, *, is_valid: bool = True):
        """Set whether the lyrics for the given lyrics_id are valid."""

        StoredLyricsQuery = Query()
        stored_lyrics = self._db.get(StoredLyricsQuery.lyrics_id == lyrics_id)

        if stored_lyrics is None:
            raise VibinNotFoundError(f"Could not find lyrics id: {lyrics_id}")

        with transaction(self._db) as tr:
            tr.update({"is_valid": is_valid}, doc_ids=[stored_lyrics.doc_id])

    def search(self, search_query: str) -> list[MediaId]:
        """Search the local lyrics database for the given search_query string.

        Returns a list of MediaIds which match the given search query.
        """

        def matches_regex(values, pattern):
            return any(
                re.search(pattern, value, flags=re.IGNORECASE) for value in values
            )

        Lyrics = Query()
        Chunk = Query()

        results = self._db.search(
            Lyrics.chunks.any(
                Chunk.header.search(search_query, flags=re.IGNORECASE)
                | Chunk.body.test(matches_regex, search_query)
            )
        )

        # Only return stored lyrics which include a media id. This is because
        # we also store lyrics from sources like Airplay and don't want to
        # return those when doing a lyrics search (the search context is
        # intended to be local media only).

        return [
            result["media_id"]
            for result in results
            if result["media_id"] is not None and result["is_valid"] is True
        ]
