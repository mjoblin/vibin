import time
from typing import Callable

from tinydb import Query
from tinydb.table import Table
from tinyrecord import transaction

from vibin import VibinNotFoundError
from vibin.mediaservers import MediaServer
from vibin.models import Album, Favorite, FavoritesPayload, Track
from vibin.types import FavoriteType, MediaId, UpdateMessageHandler
from vibin.utils import requires_media_server


class FavoritesManager:
    """Favorites manager.

    Manages the marking/unmarking and retrieval of favorite Albums and Tracks.
    Sends "Favorites" updates when favorites are changed. Favorites are stored
    in the local db.
    """

    def __init__(
        self, db: Table, media_server: MediaServer, updates_handler: UpdateMessageHandler
    ):
        self._db = db
        self._media_server = media_server
        self._updates_handler = updates_handler

    @property
    def all(self) -> list[Favorite]:
        """ All favorites."""
        return self._favorites_getter()

    @property
    def albums(self) -> list[Favorite]:
        """Favorite albums."""
        return self._favorites_getter(requested_types=["album"])

    @property
    def tracks(self) -> list[Favorite]:
        """Favorite tracks."""
        return self._favorites_getter(requested_types=["track"])

    @requires_media_server()
    def store(self, favorite_type: FavoriteType, media_id: MediaId):
        """Mark the given media_id as a favorite."""

        # Check for existing favorite with this media_id
        FavoritesQuery = Query()
        existing_favorite = self._db.get(FavoritesQuery.media_id == media_id)

        if existing_favorite:
            return

        # Check that favorite media_id exists
        media_hydrators = {
            "album": self._media_server.album,
            # "artist": self.media.artist,
            "track": self._media_server.track,
        }

        try:
            media_hydrators[favorite_type](media_id)
        except VibinNotFoundError:
            raise VibinNotFoundError(
                f"Could not find media id '{media_id}' for type '{favorite_type}'"
            )

        # Store favorite
        favorite_data = Favorite(
            type=favorite_type,
            media_id=media_id,
            when_favorited=time.time(),
        )

        with transaction(self._db) as tr:
            tr.insert(favorite_data.dict())

        self._send_update()

        return favorite_data

    def delete(self, media_id: MediaId):
        """Remove the given media_id from favorites."""

        FavoritesQuery = Query()
        favorite_to_delete = self._db.get(FavoritesQuery.media_id == media_id)

        if favorite_to_delete is None:
            raise VibinNotFoundError()

        with transaction(self._db) as tr:
            tr.remove(doc_ids=[favorite_to_delete.doc_id])

        self._send_update()

    @requires_media_server()
    def _send_update(self):
        self._updates_handler("Favorites", FavoritesPayload(favorites=self.all))

    @requires_media_server(return_val=[])
    def _favorites_getter(
        self, requested_types: list[FavoriteType] | None = None
    ) -> list[Favorite]:
        media_hydrators: dict[str, Callable[[str], Album | Track]] = {
            "album": self._media_server.album,
            "track": self._media_server.track,
        }

        return [
            Favorite(
                type=favorite["type"],
                media_id=favorite["media_id"],
                when_favorited=favorite["when_favorited"],
                media=media_hydrators[favorite["type"]](favorite["media_id"]),
            )
            for favorite in self._db.all()
            if requested_types is None or favorite["type"] in requested_types
        ]
