from vibin.mediaservers import MediaServer
from vibin.types import UpdateMessageHandler


class StoredPlaylistsManager:
    """StoredPlaylists manager.

    The provided db is expected to be a TinyDB table.
    """

    def __init__(
        self, db, media_server: MediaServer, updates_handler: UpdateMessageHandler
    ):
        self._db = db
        self._media_server = media_server
        self._updates_handler = updates_handler
