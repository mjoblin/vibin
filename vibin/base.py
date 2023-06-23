import json
import operator
import os
from pathlib import Path
import time
from typing import Any
import uuid

from tinydb import TinyDB, Query
from tinyrecord import transaction

from vibin import VibinError, VibinNotFoundError
from vibin.constants import (
    DB_ROOT,
    DEFAULT_ALL_ALBUMS_PATH,
    DEFAULT_ALL_ARTISTS_PATH,
    DEFAULT_NEW_ALBUMS_PATH,
    VIBIN_VER,
)
from vibin.device_resolution import (
    determine_media_server_class,
    determine_streamer_and_media_server,
    determine_streamer_class,
)
import vibin.external_services as external_services
from vibin.external_services import ExternalService
from vibin.logger import logger
from vibin.managers import (
    FavoritesManager,
    LinksManager,
    LyricsManager,
    StoredPlaylistsManager,
    WaveformManager,
)
from vibin.mediaservers import MediaServer
from vibin.models import (
    ActivePlaylistEntry,
    Album,
    CurrentlyPlaying,
    FavoritesPayload,
    MediaBrowseSingleLevel,
    UPnPServiceSubscriptions,
    StoredPlaylist,
    StoredPlaylists,
    StoredPlaylistStatus,
    SystemState,
    Track,
    UpdateMessage,
    VibinSettings,
)
from vibin.streamers import Streamer
from vibin.types import (
    MediaId,
    PlaylistModifyAction,
    UpdateMessageHandler,
    UPnPDeviceType,
    UpdateMessageType,
)
from vibin.utils import requires_media_server


class Vibin:
    def __init__(
        self,
        streamer: str | None = None,
        streamer_type: str | None = None,
        media_server: str | bool | None = None,
        media_server_type: str | None = None,
        discovery_timeout: int = 5,
        upnp_subscription_callback_base: str | None = None,
    ):
        """The main Vibin class.

        Responsibilities include:

            * Handling discovery and management of the Streamer and MediaServer.
            * Managing external services (Genius, Discogs, etc).
            * Managing playlists and favorites.
            * UpdateMessage handling:
                * Receiving UpdateMessages from the Streamer and MediaServer and
                  forwarding them to any registered handlers.
                * Passing its own UpdateMessages to any registered handlers.
            * Providing convenience methods for media playback.
        """
        logger.info(f"Initializing Vibin v{VIBIN_VER}")

        self._on_update_handlers: list[UpdateMessageHandler] = []
        self._last_played_id = None

        # Configure external services
        self._external_services: dict[str, ExternalService] = {}

        self._add_external_service(external_services.Discogs, "DISCOGS_ACCESS_TOKEN")
        self._add_external_service(external_services.Genius, "GENIUS_ACCESS_TOKEN")
        self._add_external_service(external_services.RateYourMusic, None)
        self._add_external_service(external_services.Wikipedia, None)

        self._stored_playlist_status = StoredPlaylistStatus()
        self._ignore_playlist_updates = False
        self._cached_stored_playlist: StoredPlaylist | None = None
        self._init_db()

        # Set up the Streamer and MediaServer instances
        self._current_streamer: Streamer | None = None
        self._current_media_server: MediaServer | None = None

        streamer_device, media_server_device = determine_streamer_and_media_server(
            streamer, media_server, discovery_timeout
        )

        # Streamer
        if streamer_device is None:
            raise VibinError("Could not find streamer on the network")

        streamer_class = determine_streamer_class(streamer_device, streamer_type)

        # Create an instance of the Streamer subclass which we can use to
        # manage our streamer.
        self._current_streamer = streamer_class(
            device=streamer_device,
            upnp_subscription_callback_base=f"{upnp_subscription_callback_base}/streamer",
            on_update=self._on_streamer_update,
            on_playlist_modified=self._on_playlist_modified,
        )

        logger.info(
            f"Using streamer UPnP device: {self.streamer.name} ({type(self.streamer).__name__})"
        )

        # MediaServer
        if media_server_device:
            media_server_class = determine_media_server_class(
                media_server_device, media_server_type
            )

            # Create an instance of the MediaServer subclass which we can use to
            # manage our media device
            self._current_media_server = media_server_class(
                device=media_server_device,
                upnp_subscription_callback_base=f"{upnp_subscription_callback_base}/media_server",
                on_update=self._on_media_server_update,
            )

            # Register the media server with the streamer.
            self._current_streamer.register_media_server(self._current_media_server)

            settings = self.settings
            self._current_media_server.all_albums_path = settings.all_albums_path
            self._current_media_server.new_albums_path = settings.new_albums_path
            self._current_media_server.all_artists_path = settings.all_artists_path

            logger.info(
                f"Using media server UPnP device: {self.media_server.name} "
                + f"({type(self.media_server).__name__})"
            )
        else:
            logger.warning(
                "Not using a local media server; some features will be unavailable"
            )

        # Initialize the managers for features like favorites, playlists, etc.
        self.favorites_manager = FavoritesManager(
            db=self._favorites_db,
            media_server=self.media_server,
            updates_handler=self._send_update,
        )

        self.links_manager = LinksManager(
            db=self._links_db,
            media_server=self.media_server,
            external_services=self._external_services,
        )

        self.lyrics_manager = LyricsManager(
            db=self._lyrics_db, genius_service=self._external_services.get("Genius")
        )

        self.stored_playlists_manager = StoredPlaylistsManager(
            db=self._playlists_db,
            media_server=self.media_server,
            updates_handler=self._send_update,
        )

        self.waveform_manager = WaveformManager(media_server=self.media_server)

        # Additional initialization
        self._check_for_active_playlist_in_store()
        self._subscribe_to_upnp_events()

    def __str__(self):
        return (
            f"Vibin: "
            + f"streamer:'{'None' if self.streamer is None else self.streamer.name}'; "
            + f"media server:'{'None' if self.media_server is None else self.media_server.name}'"
        )

    @property
    def streamer(self) -> Streamer:
        """The Streamer instance. Provides access to Streamer capabilities."""
        return self._current_streamer

    @property
    def media_server(self) -> MediaServer:
        """The MediaServer instance. Provides access to MediaServer capabilities."""
        return self._current_media_server

    @property
    def settings(self) -> VibinSettings:
        """Current Vibin system settings."""
        return VibinSettings.parse_obj(self._settings_db.all()[0])

    @settings.setter
    def settings(self, settings: VibinSettings):
        with transaction(self._settings_db) as tr:
            tr.update(settings.dict())

        self._current_media_server.all_albums_path = settings.all_albums_path
        self._current_media_server.new_albums_path = settings.new_albums_path
        self._current_media_server.all_artists_path = settings.all_artists_path

    @property
    def system_state(self) -> SystemState:
        """The current system state."""
        return SystemState(
            streamer=self.streamer.device_state,
            media=self.media_server.device_state,
        )

    @property
    def upnp_properties(self):
        """The current UPnP properties for the Streamer and MediaServer devices."""

        all_upnp_properties = {
            "streamer": self.streamer.upnp_properties,
            "media_server": self.media_server.upnp_properties if self.media_server else None,
        }

        return all_upnp_properties

    def get_current_state_messages(self) -> list[UpdateMessage]:
        """Return a list of UpdateMessages reflecting the current state of the
        system."""

        return [
            UpdateMessage(message_type="System", payload=self.system_state),
            UpdateMessage(message_type="UPnPProperties", payload=self.upnp_properties),
            UpdateMessage(
                message_type="TransportState", payload=self.streamer.transport_state
            ),
            UpdateMessage(message_type="CurrentlyPlaying", payload=self.currently_playing),
            UpdateMessage(
                message_type="Favorites",
                payload=FavoritesPayload(favorites=self.favorites_manager.all),
            ),
            UpdateMessage(message_type="Presets", payload=self.streamer.presets),
            UpdateMessage(message_type="StoredPlaylists", payload=self.stored_playlists),
        ]

    @property
    def currently_playing(self) -> CurrentlyPlaying:
        """What is currently playing on the streamer."""
        return self.streamer.currently_playing

    @requires_media_server()
    def browse_media(self, parent_id: str = "0") -> MediaBrowseSingleLevel:
        """Retrieve the children of the given parent_id on the MediaServer."""
        return self.media_server.children(parent_id)

    @requires_media_server()
    def play_album(self, album: Album):
        """Play the provided Album. This replaces the active streamer playlist."""
        self.play_id(album.id)

    @requires_media_server()
    def play_track(self, track: Track):
        """Play the provided Track. This replaces the active streamer playlist."""
        self.play_id(track.id)

    @requires_media_server()
    def play_id(self, id: MediaId):
        """Play the provided media ID. This replaces the active streamer playlist."""
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.modify_playlist(self.media_server.get_metadata(id))
        self._last_played_id = id

    @requires_media_server()
    def play_ids(self, media_ids: list[MediaId], max_count: int = 10):
        """Play the provided media IDs. This replaces the active streamer playlist."""
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.playlist_clear()

        # TODO: Consider adding a hard max_count limit
        for media_id in media_ids[:max_count]:
            self.modify_playlist(media_id, "APPEND")

        if len(media_ids) > 0:
            self.streamer.play_playlist_index(0)
            self._last_played_id = media_ids[0]
        else:
            self._last_played_id = None

    @requires_media_server()
    def play_favorite_albums(self, max_count: int = 10):
        """Play all favorited Albums (up to max_count)."""
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.playlist_clear()

        # TODO: Consider adding a hard max_count limit
        for album in self.favorites_manager.albums[:max_count]:
            self.modify_playlist(album["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    @requires_media_server()
    def play_favorite_tracks(self, max_count: int = 100):
        """Play all favorited Tracks (up to max_count)."""
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.playlist_clear()

        # TODO: Consider adding a hard max_count limit
        for track in self.favorites_manager.tracks[:max_count]:
            self.modify_playlist(track["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    def on_update(self, handler: UpdateMessageHandler):
        """Register a handler to receive system update messages.

        Each registered handler will be sent each UpdateMessage as they are
        emitted from the system. An UpdateMessage is emitted on events such as
        a track change, transport change (pause, play), playlist update, etc.
        (see UpdateMessageType for a complete list).
        """
        self._on_update_handlers.append(handler)

    def on_upnp_event(self, device: UPnPDeviceType, service_name: str, event: str):
        """Handle an incoming UPnP event from one of the UPnP devices.

        The device will be either the Streamer or the MediaServer. The
        service_name and event are UPnP concepts which will depend on what
        events the device has subscribed to.
        """
        # Extract the event.
        upnp_subscriptions: UPnPServiceSubscriptions = {}

        if device == "streamer":
            upnp_subscriptions = self.streamer.upnp_subscriptions
        elif device == "media_server":
            upnp_subscriptions = self.media_server.upnp_subscriptions

        if not upnp_subscriptions:
            logger.warning(
                f"UPnP event received for device with no subscriptions: {device}"
            )
        else:
            # Pass event to the device to handle.
            subscribed_service_names = [service.name for service in upnp_subscriptions.keys()]

            if service_name in subscribed_service_names:
                if device == "streamer":
                    self.streamer.on_upnp_event(service_name, event)
                elif device == "media_server":
                    self.media_server.on_upnp_event(service_name, event)
            else:
                logger.warning(
                    f"UPnP event received for device ({device}) with no subscription handler "
                    + f"for the {service_name} service: {device}"
                )

            # Send updated state vars to interested recipients.
            self._send_update("UPnPProperties", self.upnp_properties)

    def db_get(self):
        """Retrieve the entire system database as a dict."""
        # NOTE: TinyDB isn't thread safe, and this code doesn't use tinyrecord,
        #   so it could in theory produce an incomplete result.
        with open(self._db_file, "r") as fh:
            return json.loads(fh.read())

    def db_set(self, data):
        """Set the entire system database from a dict."""
        # NOTE: TinyDB isn't thread safe, and this code doesn't use tinyrecord,
        #   so it could in theory corrupt the database.
        with open(self._db_file, "w") as fh:
            fh.write(json.dumps(data))

        self._init_db()

    def shutdown(self):
        """Shut down the Vibin system.

        Handles the system shutdown process, including:

        * Closing the database connection.
        * Disconnecting from the Streamer.
        """
        logger.info("Vibin is shutting down")

        logger.info("Closing database")
        self._db.close()

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.on_shutdown()

        logger.info("Vibin shutdown complete")

    # -------------------------------------------------------------------------
    # Initialization helpers

    def _init_db(self):
        # Configure app-level persistent data directory.
        try:
            os.makedirs(DB_ROOT, exist_ok=True)
        except OSError:
            raise VibinError(f"Cannot create data directory: {DB_ROOT}")

        # Configure data store.
        self._db_file = Path(DB_ROOT, "db.json")
        self._db = TinyDB(self._db_file)
        self._settings_db = self._db.table("settings")
        self._playlists_db = self._db.table("playlists")
        self._favorites_db = self._db.table("favorites")
        self._lyrics_db = self._db.table("lyrics")
        self._links_db = self._db.table("links")

        if len(self._settings_db.all()) == 0:
            settings = VibinSettings(
                all_albums_path=DEFAULT_ALL_ALBUMS_PATH,
                new_albums_path=DEFAULT_NEW_ALBUMS_PATH,
                all_artists_path=DEFAULT_ALL_ARTISTS_PATH,
            )

            with transaction(self._settings_db) as tr:
                tr.insert(settings.dict())

    def _add_external_service(self, service_class, token_env_var=None):
        try:
            service_instance = service_class(
                user_agent=f"vibin/{VIBIN_VER}",
                token=os.environ[token_env_var] if token_env_var else None,
            )

            self._external_services[service_instance.name] = service_instance

            logger.info(f"Registered external service: {service_instance.name}")
        except KeyError:
            pass

    def _subscribe_to_upnp_events(self):
        """Instruct the streamer and media server to subscribe to UPnP events.

        NOTE: This instructs the streamer and media server to configure their
            UPnP subscriptions, which will in turn trigger the UPnP services
            to start sending events to Vibin's upnp event receiver endpoint.
            That endpoint might not be ready to receive requests yet, so some
            initial events might get dropped. It would be nice if this could
            be delayed until FastAPI is ready to receive requests.
        """
        self.streamer.subscribe_to_upnp_events()

        if self.media_server:
            self.media_server.subscribe_to_upnp_events()

    # -------------------------------------------------------------------------
    # Private methods

    def _on_streamer_update(self, message_type: UpdateMessageType, data: Any):
        # Forward streamer updates directly to all update subscribers.
        self._send_update(message_type, data)

    def _on_media_server_update(self, message_type: UpdateMessageType, data: Any):
        # Forward media server updates directly to all update subscribers.
        self._send_update(message_type, data)

    def _send_update(self, message_type: UpdateMessageType, data: Any):
        for handler in self._on_update_handlers:
            handler(message_type, data)

    # -------------------------------------------------------------------------

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

    def _check_for_active_playlist_in_store(self):
        # See if the current streamer playlist matches a stored playlist
        # streamer_playlist = self.streamer.playlist(call_handler_on_sync_loss)
        streamer_playlist_entries = self.streamer.playlist.entries

        if len(streamer_playlist_entries) <= 0:
            # self._active_stored_playlist_id = None
            # self._active_playlist_synced_with_store = False
            self._reset_stored_playlist_status(send_update=True)
            return

        # See if there's a stored playlist which matches the currently-active
        # streamer playlist (same media ids in the same order). If there's more
        # than one, then pick the one most recently updated.
        active_playlist_media_ids = [
            entry.trackMediaId for entry in streamer_playlist_entries
        ]

        stored_playlists_as_dicts = [StoredPlaylist(**p) for p in self._playlists_db.all()]

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

            # self._active_stored_playlist_id = stored_playlist_matching_active.id
            # self._active_playlist_synced_with_store = True
            self._stored_playlist_status.active_id = stored_playlist_matching_active.id
            self._stored_playlist_status.is_active_synced_with_store = True
            self._cached_stored_playlist = stored_playlist_matching_active
        except IndexError:
            # self._active_playlist_synced_with_store = False
            #
            # if no_active_if_not_found:
            #     self._active_stored_playlist_id = None
            self._reset_stored_playlist_status(send_update=False)
            self._cached_stored_playlist = None

        self._send_stored_playlists_update()

    @requires_media_server()
    def _send_stored_playlists_update(self):
        self._send_update("StoredPlaylists", self.stored_playlists)

    @requires_media_server()
    def modify_playlist(
        self,
        id: MediaId,
        action: PlaylistModifyAction = "REPLACE",
        insert_index: int | None = None,
    ):
        """Modify the streamer's active playlist."""
        self.streamer.modify_playlist(self.media_server.get_metadata(id), action, insert_index)

        if action == "REPLACE":
            self._reset_stored_playlist_status(send_update=True)

    @property
    def stored_playlists(self) -> StoredPlaylists:
        return StoredPlaylists(
            status=self._stored_playlist_status,
            playlists=[StoredPlaylist(**playlist) for playlist in self._playlists_db.all()],
        )

    def _streamer_playlist_matches_stored(
        self, streamer_playlist: list[ActivePlaylistEntry]
    ):
        if not self._cached_stored_playlist:
            return False

        streamer_playlist_ids = [entry.trackMediaId for entry in streamer_playlist]
        stored_playlist_ids = self._cached_stored_playlist.entry_ids

        return streamer_playlist_ids == stored_playlist_ids

    def _on_playlist_modified(self, playlist_entries: list[ActivePlaylistEntry]):
        if (
            not self._ignore_playlist_updates
            and self._stored_playlist_status.active_id
            and self.streamer
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

            if self._stored_playlist_status.active_id:
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

    def playlists(self) -> list[StoredPlaylist]:
        return [StoredPlaylist(**playlist_dict) for playlist_dict in self._playlists_db.all()]

    def get_playlist(self, playlist_id) -> StoredPlaylist:
        PlaylistQuery = Query()
        playlist_dict = self._playlists_db.get(PlaylistQuery.id == playlist_id)

        if playlist_dict is None:
            raise VibinNotFoundError()

        return StoredPlaylist(**playlist_dict)

    @requires_media_server()
    def set_active_playlist(self, playlist_id: str) -> StoredPlaylist:
        self._reset_stored_playlist_status(is_activating=True, send_update=True)

        PlaylistQuery = Query()
        playlist_dict = self._playlists_db.get(PlaylistQuery.id == playlist_id)

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
        self.streamer.playlist_clear()

        self._ignore_playlist_updates = True

        for entry_id in playlist.entry_ids:
            self.streamer.modify_playlist(
                self.media_server.get_metadata(entry_id), action="APPEND"
            )

        self._ignore_playlist_updates = False

        self._reset_stored_playlist_status(
            active_id=playlist_id, is_synced=True, is_activating=False, send_update=True
        )

        return StoredPlaylist(**playlist_dict)

    @requires_media_server()
    def store_active_playlist(
        self,
        metadata: dict[str, any] | None = None,
        replace: bool = True,
    ) -> StoredPlaylist:
        active_playlist = self.streamer.playlist
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

            with transaction(self._playlists_db) as tr:
                tr.insert(playlist_data.dict())

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
                with transaction(self._playlists_db) as tr:
                    doc_id = tr.update(
                        updates,
                        PlaylistQuery.id == self._stored_playlist_status.active_id,
                    )[0]

                playlist_data = StoredPlaylist(**self._playlists_db.get(doc_id=doc_id))
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
    def delete_playlist(self, playlist_id: str):
        PlaylistQuery = Query()
        playlist_to_delete = self._playlists_db.get(PlaylistQuery.id == playlist_id)

        if playlist_to_delete is None:
            raise VibinNotFoundError()

        with transaction(self._playlists_db) as tr:
            tr.remove(doc_ids=[playlist_to_delete.doc_id])

        self._send_stored_playlists_update()

    def update_playlist_metadata(
        self, playlist_id: str, metadata: dict[str, any]
    ) -> StoredPlaylist:
        now = time.time()
        PlaylistQuery = Query()

        try:
            with transaction(self._playlists_db) as tr:
                updated_ids = tr.update(
                    {
                        "updated": now,
                        "name": metadata["name"],
                    },
                    PlaylistQuery.id == playlist_id,
                )

            if updated_ids is None or len(updated_ids) <= 0:
                raise VibinNotFoundError()

            self._send_stored_playlists_update()

            return StoredPlaylist(**self._playlists_db.get(doc_id=updated_ids[0]))
        except IndexError:
            raise VibinError(f"Could not update Playlist Id: {playlist_id}")
