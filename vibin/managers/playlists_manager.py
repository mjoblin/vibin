import operator
import time
import uuid

from tinydb import Query
from tinydb.table import Table

from vibin import VibinError, VibinNotFoundError
from vibin.mediaservers import MediaServer
from vibin.models import (
    ActivePlaylistEntry,
    StoredPlaylist,
    StoredPlaylists,
    StoredPlaylistStatus,
)
from vibin.streamers import Streamer
from vibin.types import MediaId, PlaylistModifyAction, UpdateMessageHandler
from vibin.utils import DB_ACCESS_LOCK_PLAYLISTS, requires_media_server


class PlaylistsManager:
    """Playlists manager.

    Manages both the active streamer queue and stored playlists.

    Notes on the streamer's active queue vs. stored playlists:

    The streamer has a single active queue, which represents all the tracks
    currently configured to be played on the streamer. Playing an album usually
    means replacing the streamer's active queue with all the tracks on the
    album. It's also possible to make other changes to the streamer's active
    queue, such as inserting a new track, removing tracks, etc.

    Stored playlists are a list of track ids persisted to the database. A
    stored playlist can be named, and multiple stored playlists can be kept
    over time. Stored playlists can then be used to set the streamer's single
    active queue (called "activating" the stored playlist). Activating a stored
    playlist results in replacing the streamer's active queue with all the
    track ids found in the stored playlist.

    Active streamer queue capabilities include:

        * Retrieving the streamer's active queue
        * Clearing the queue
        * Modifying the queue (replace, append, insert, etc)
        * Playing a queue index

    Stored playlist capabilities include:

        * Retrieving stored playlist details
        * Activating a stored playlist (i.e. making a stored playlist the
          streamer's active playlist)
        * Persisting the streamer's active playlist as a stored playlist
        * Deleting a stored playlist
        * Updating the metadata on a stored playlist (e.g. playlist name)
        * Checking whether the streamer's active playlist matches an existing
          stored playlist
        * Sending "StoredPlaylists" update messages when stored playlists are
          changed

    The playlists manager also tracks whether a stored playlist is being
    activated; whether the active playlist has drifted from matching a stored
    playlist; etc. See self._stored_playlist_status.
    """

    def __init__(
        self,
        db: Table,
        streamer: Streamer,
        media_server: MediaServer,
        updates_handler: UpdateMessageHandler,
    ):
        self._db = db
        self._streamer = streamer
        self._media_server = media_server
        self._updates_handler = updates_handler

        self._stored_playlist_status = StoredPlaylistStatus()
        self._cached_stored_playlist: StoredPlaylist | None = None
        self._ignore_playlist_updates = False

    def clear_streamer_queue(self):
        """Clear the streamer's active playlist."""
        self._reset_stored_playlist_status(send_update=True)
        self._streamer.playlist_clear()

    @requires_media_server()
    def modify_streamer_queue(
        self,
        metadata: str,
        action: PlaylistModifyAction = "REPLACE",
        play_from_id: MediaId | None = None,
    ):
        """Modify the streamer's active queue.

        Metadata is expected to be the DIDL-Lite XML media server metadata for
        the media item to be added to the queue. Possible actions include
        "APPEND", "PLAY_FROM_HERE", "PLAY_NEXT", "PLAY_NOW", "REPLACE".

        Args:
            metadata: DIDL-Lite XML metadata for the media.
            action: How to modify the queue.
            play_from_id: Only used with PLAY_FROM_HERE action. Specifies the
                track ID within an album to start playing from.
        """
        self._streamer.modify_queue(metadata, action, play_from_id)

        if action == "REPLACE":
            # Treat an entire playlist replace as cutting any connection to a
            # stored playlist. It's possible that the resulting playlist update
            # details (sent by the streamer and received by
            # on_streamer_playlist_modified()) will detect the new playlist as
            # being identical to a stored playlist; but we don't know if that's
            # the case yet.
            self._reset_stored_playlist_status(send_update=True)

    @requires_media_server()
    def modify_streamer_queue_with_id(
        self,
        id: MediaId,
        action: PlaylistModifyAction = "REPLACE",
        play_from_id: MediaId | None = None,
    ):
        """Modify the streamer's active queue.

        The same as modify_streamer_queue() except using MediaId instead of
        media metadata.
        """
        self.modify_streamer_queue(
            self._media_server.get_metadata(id),
            action,
            play_from_id,
        )

    def play_streamer_queue_index(self, index: int):
        """Play the given index in the streamer's active queue."""
        self._streamer.play_playlist_index(index)

    @property
    def stored_playlists(self) -> StoredPlaylists:
        """Details on all stored playlists."""
        with DB_ACCESS_LOCK_PLAYLISTS:
            playlists = StoredPlaylists(
                status=self._stored_playlist_status,
                playlists=[StoredPlaylist(**playlist) for playlist in self._db.all()],
            )

        return playlists

    def get_stored_playlist(self, playlist_id) -> StoredPlaylist:
        """Details on a single stored playlist."""
        PlaylistQuery = Query()

        with DB_ACCESS_LOCK_PLAYLISTS:
            playlist_dict = self._db.get(PlaylistQuery.id == playlist_id)

        if playlist_dict is None:
            raise VibinNotFoundError()

        return StoredPlaylist(**playlist_dict)

    @requires_media_server()
    def activate_stored_playlist(self, stored_playlist_id: str) -> StoredPlaylist:
        """Set the streamer's active queue to the items in a stored playlist."""
        self.clear_streamer_queue()
        self._reset_stored_playlist_status(is_activating=True, send_update=True)

        PlaylistQuery = Query()

        with DB_ACCESS_LOCK_PLAYLISTS:
            playlist_dict = self._db.get(PlaylistQuery.id == stored_playlist_id)

        if playlist_dict is None:
            raise VibinNotFoundError()

        playlist = StoredPlaylist(**playlist_dict)
        self._cached_stored_playlist = playlist

        # Add each playlist entry to the streamer's active playlist. Ideally
        # this could be batched in one request, but it looks like they need to
        # be added individually. Adding entries individually means we'll get
        # notified by the streamer for every newly added entry, which in turn
        # triggers us notifying any connected clients. This could result in
        # many superfluous notifications going out to the clients, so we hack
        # in an "ignore playlist updates" boolean while entries are added. This
        # isn't clean, and the boolean will be set back to False again before
        # the system has fully dealt with adding entries to the active playlist
        # -- but it's better than doing nothing.

        self._ignore_playlist_updates = True

        for entry_id in playlist.entry_ids:
            self._streamer.modify_queue(
                self._media_server.get_metadata(entry_id), action="APPEND"
            )

        self._ignore_playlist_updates = False

        self._reset_stored_playlist_status(
            active_id=stored_playlist_id, is_synced=True, is_activating=False, send_update=True
        )

        return StoredPlaylist(**playlist_dict)

    @requires_media_server()
    def store_streamer_queue_as_playlist(
        self,
        metadata: dict[str, any] | None = None,
        replace: bool = True,
    ) -> StoredPlaylist:
        """Store the streamer's active queue as a stored playlist."""
        active_playlist = self._streamer.playlist
        now = time.time()
        new_playlist_id = str(uuid.uuid4())

        if self._stored_playlist_status.active_id is None or replace is False:
            # Brand new stored playlist
            playlist_data = StoredPlaylist(
                id=new_playlist_id,
                name=metadata["name"] if metadata and "name" in metadata else "Unnamed",
                created=now,
                updated=now,
                entry_ids=[entry.trackMediaId for entry in active_playlist.entries],
            )

            with DB_ACCESS_LOCK_PLAYLISTS:
                self._db.insert(playlist_data.dict())

            self._cached_stored_playlist = playlist_data

            self._reset_stored_playlist_status(
                active_id=new_playlist_id,
                is_synced=True,
                is_activating=False,
                send_update=True,
            )
        else:
            # Updates to an existing playlist
            updates = {
                "updated": now,
                "entry_ids": [entry.trackMediaId for entry in active_playlist.entries],
            }

            if metadata and "name" in metadata:
                updates["name"] = metadata["name"]

            PlaylistQuery = Query()

            try:
                with DB_ACCESS_LOCK_PLAYLISTS:
                    doc_id = self._db.update(
                        updates,
                        PlaylistQuery.id == self._stored_playlist_status.active_id,
                    )[0]

                    playlist_data = StoredPlaylist(**self._db.get(doc_id=doc_id))

                self._cached_stored_playlist = playlist_data
            except IndexError:
                self._reset_stored_playlist_status(
                    active_id=None,
                    is_synced=False,
                    is_activating=False,
                    send_update=True,
                )

                raise VibinError(
                    f"Could not update Playlist Id: {self._stored_playlist_status.active_id}"
                )

            self._reset_stored_playlist_status(
                active_id=self._stored_playlist_status.active_id,
                is_synced=True,
                is_activating=False,
                send_update=True,
            )

        return playlist_data

    @requires_media_server()
    def delete_stored_playlist(self, playlist_id: str):
        """Delete a stored playlist."""
        PlaylistQuery = Query()

        with DB_ACCESS_LOCK_PLAYLISTS:
            playlist_to_delete = self._db.get(PlaylistQuery.id == playlist_id)

            if playlist_to_delete is None:
                raise VibinNotFoundError()

            self._db.remove(doc_ids=[playlist_to_delete.doc_id])

        self._send_stored_playlists_update()

    def update_stored_playlist_metadata(
        self, playlist_id: str, metadata: dict[str, any]
    ) -> StoredPlaylist:
        """Update the metadata of a stored playlist.

        Currently, the only supported metadata update key is "name".
        """
        now = time.time()
        PlaylistQuery = Query()

        try:
            with DB_ACCESS_LOCK_PLAYLISTS:
                updated_ids = self._db.update(
                    {
                        "updated": now,
                        "name": metadata["name"],
                    },
                    PlaylistQuery.id == playlist_id,
                )

            if updated_ids is None or len(updated_ids) <= 0:
                raise VibinNotFoundError()

            self._send_stored_playlists_update()

            with DB_ACCESS_LOCK_PLAYLISTS:
                playlist = StoredPlaylist(**self._db.get(doc_id=updated_ids[0]))

            return playlist
        except IndexError:
            raise VibinError(f"Could not update Playlist Id: {playlist_id}")

    def check_for_streamer_playlist_in_store(self):
        """Check if the streamer's active queue matching a stored playlist."""
        streamer_playlist_entries = self._streamer.playlist.entries

        if len(streamer_playlist_entries) <= 0:
            self._reset_stored_playlist_status(send_update=True)
            return

        # See if there's a stored playlist which matches the currently-active
        # streamer playlist (same media ids in the same order). If there's more
        # than one, then pick the one most recently updated.
        active_playlist_media_ids = [
            entry.trackMediaId for entry in streamer_playlist_entries
        ]

        with DB_ACCESS_LOCK_PLAYLISTS:
            stored_playlists_as_dicts = [StoredPlaylist(**p) for p in self._db.all()]

        try:
            stored_playlist_matching_active = sorted(
                [
                    playlist
                    for playlist in stored_playlists_as_dicts
                    if playlist.entry_ids == active_playlist_media_ids
                ],
                key=operator.attrgetter("updated"),
                reverse=True,
            )[0]

            self._stored_playlist_status.active_id = stored_playlist_matching_active.id
            self._stored_playlist_status.is_active_synced_with_store = True
            self._cached_stored_playlist = stored_playlist_matching_active
        except IndexError:
            self._reset_stored_playlist_status(send_update=False)
            self._cached_stored_playlist = None

        self._send_stored_playlists_update()

    def on_streamer_playlist_modified(
        self, playlist_entries: list[ActivePlaylistEntry]
    ):
        """Invoked whenever the streamer's active queue is changed.

        Determines whether the changed queue on the streamer should update
        the current stored playlist status information.
        """
        if (
            not self._ignore_playlist_updates
            and self._stored_playlist_status.active_id
            and self._streamer
        ):
            # The playlist has been modified. If a stored playlist is active
            # then compare this playlist against the stored playlist and
            # set the status appropriately. The goal here is to ensure that
            # we catch the playlist differing from the stored playlist, or
            # matching the stored playlist (which can happen during playlist
            # editing when entries are moved, deleted, added, etc).

            # NOTE:
            #
            # If Vibin is tracking an active stored playlist, and another app
            # replaces the streamer playlist, then Vibin will treat that
            # replacement as an *update to the active stored playlist* rather
            # than a "replace playlist and no longer consider this an active
            # stored playlist" action. The playlist changes won't actually be
            # persisted unless the user requests it, but the behavior might
            # feel inconsistent.

            prior_sync_state = (
                self._stored_playlist_status.is_active_synced_with_store
            )

            self._stored_playlist_status.is_active_synced_with_store = (
                self._streamer_playlist_matches_stored(playlist_entries)
            )

            if (
                self._stored_playlist_status.is_active_synced_with_store
                != prior_sync_state
            ):
                self._send_stored_playlists_update()

    # -------------------------------------------------------------------------
    # Private methods

    @requires_media_server()
    def _send_stored_playlists_update(self):
        self._updates_handler("StoredPlaylists", self.stored_playlists)

    def _streamer_playlist_matches_stored(
        self, streamer_playlist: list[ActivePlaylistEntry]
    ):
        if not self._cached_stored_playlist:
            return False

        streamer_playlist_ids = [entry.trackMediaId for entry in streamer_playlist]
        stored_playlist_ids = self._cached_stored_playlist.entry_ids

        return streamer_playlist_ids == stored_playlist_ids

    def _reset_stored_playlist_status(
        self,
        active_id=None,
        is_synced=False,
        is_activating=False,
        send_update=False,
    ):
        self._stored_playlist_status.active_id = active_id
        self._stored_playlist_status.is_active_synced_with_store = is_synced
        self._stored_playlist_status.is_activating_playlist = is_activating

        if send_update:
            self._send_stored_playlists_update()
