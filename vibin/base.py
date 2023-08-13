import json
import os
from pathlib import Path
from typing import Any

from tinydb import TinyDB

from vibin import VibinError
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
    PlaylistsManager,
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
    SystemState,
    Track,
    UpdateMessage,
    VibinSettings,
)
from vibin.streamers import Streamer
from vibin.types import (
    MediaId,
    UpdateMessageHandler,
    UPnPDeviceType,
    UpdateMessageType,
)
from vibin.utils import DB_ACCESS_LOCK, requires_media_server


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
            on_playlist_modified=self._on_streamer_playlist_modified,
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
        self._favorites_manager = FavoritesManager(
            db=self._favorites_db,
            media_server=self.media_server,
            updates_handler=self._send_update,
        )

        self._links_manager = LinksManager(
            db=self._links_db,
            media_server=self.media_server,
            external_services=self._external_services,
        )

        self._lyrics_manager = LyricsManager(
            db=self._lyrics_db,
            media_server=self.media_server,
            genius_service=self._external_services.get("Genius"),
        )

        self._playlists_manager = PlaylistsManager(
            db=self._playlists_db,
            media_server=self.media_server,
            streamer=self.streamer,
            updates_handler=self._send_update,
        )

        self._waveform_manager = WaveformManager(media_server=self.media_server)

        # Additional initialization
        self.playlists_manager.check_for_streamer_playlist_in_store()
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
    def favorites_manager(self) -> FavoritesManager:
        """The Favorites manager."""
        return self._favorites_manager

    @property
    def links_manager(self) -> LinksManager:
        """The Links manager."""
        return self._links_manager

    @property
    def lyrics_manager(self) -> LyricsManager:
        """The Lyrics manager."""
        return self._lyrics_manager

    @property
    def playlists_manager(self) -> PlaylistsManager:
        """The Playlists manager."""
        return self._playlists_manager

    @property
    def waveform_manager(self) -> WaveformManager:
        """The Waveform manager."""
        return self._waveform_manager

    @property
    def settings(self) -> VibinSettings:
        """Current Vibin system settings."""
        return VibinSettings.parse_obj(self._settings_db.all()[0])

    @settings.setter
    def settings(self, settings: VibinSettings):
        with DB_ACCESS_LOCK:
            self._settings_db.update(settings.dict())

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
            UpdateMessage(
                message_type="StoredPlaylists",
                payload=self.playlists_manager.stored_playlists,
            ),
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
    def play_id(self, media_id: MediaId):
        """Play the provided media ID. This replaces the active streamer playlist."""
        self.playlists_manager.modify_streamer_playlist_with_id(media_id)
        self._last_played_id = media_id

    @requires_media_server()
    def play_ids(self, media_ids: list[MediaId], max_count: int = 10):
        """Play the provided media IDs. This replaces the active streamer playlist."""
        self.playlists_manager.clear_streamer_playlist()

        # TODO: Consider adding a hard max_count limit
        for media_id in media_ids[:max_count]:
            self.playlists_manager.modify_streamer_playlist_with_id(
                media_id, "APPEND", ignore_stored_playlist_impact=True
            )

        if len(media_ids) > 0:
            self.playlists_manager.play_streamer_playlist_index(0)
            self._last_played_id = media_ids[0]
        else:
            self._last_played_id = None

    @requires_media_server()
    def play_favorite_albums(self, max_count: int = 10):
        """Play all favorited Albums (up to max_count)."""
        self.playlists_manager.clear_streamer_playlist()

        # TODO: Consider adding a hard max_count limit
        for album in self.favorites_manager.albums[:max_count]:
            self.playlists_manager.modify_streamer_playlist_with_id(
                album["media_id"], "APPEND", ignore_stored_playlist_impact=True
            )

        self.playlists_manager.play_streamer_playlist_index(0)

    @requires_media_server()
    def play_favorite_tracks(self, max_count: int = 100):
        """Play all favorited Tracks (up to max_count)."""
        self.playlists_manager.clear_streamer_playlist()

        # TODO: Consider adding a hard max_count limit
        for track in self.favorites_manager.tracks[:max_count]:
            self.playlists_manager.modify_streamer_playlist_with_id(
                track["media_id"], "APPEND", ignore_stored_playlist_impact=True
            )

        self.playlists_manager.play_streamer_playlist_index(0)

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
        with DB_ACCESS_LOCK, open(self._db_file, "r") as fh:
            return json.loads(fh.read())

    def db_set(self, data):
        """Set the entire system database from a dict."""
        with DB_ACCESS_LOCK, open(self._db_file, "w") as fh:
            fh.write(json.dumps(data))

        self._init_db()

    def shutdown(self):
        """Shut down the Vibin system.

        Handles the system shutdown process, including:

        * Closing the database connection.
        * Disconnecting from the Streamer.
        """
        logger.info("Vibin instance is shutting down")

        logger.info("Closing database")
        self._db.close()

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.on_shutdown()

        logger.info("Vibin instance shutdown complete")

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

            with DB_ACCESS_LOCK:
                self._settings_db.insert(settings.dict())

    def _add_external_service(self, service_class, token_env_var=None):
        try:
            service_instance = service_class(
                user_agent=f"vibin/{VIBIN_VER}",
                token=os.environ[token_env_var] if token_env_var else None,
            )

            self._external_services[service_instance.name] = service_instance

            logger.info(f"Registered external service: {service_instance.name}")
        except KeyError:
            logger.warning(
                f"Not registering external service: {service_class.__name__}. "
                + f"Missing required token env var {token_env_var}?"
            )

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
    # Additional Private methods

    def _on_streamer_update(self, message_type: UpdateMessageType, data: Any):
        """Handle an incoming update message from the streamer."""
        self._send_update(message_type, data)

    def _on_media_server_update(self, message_type: UpdateMessageType, data: Any):
        """Handle an incoming update message from the media server."""
        self._send_update(message_type, data)

    def _on_streamer_playlist_modified(self, playlist_entries: list[ActivePlaylistEntry]):
        """Handle information on a change to the streamer's active playlist."""

        # Forward the change information to the playlist manager. Note that
        # it's possible for this handler to be called before the playlist
        # manager has been initialized, so check first.
        if hasattr(self, "playlists_manager"):
            self.playlists_manager.on_streamer_playlist_modified(playlist_entries)

    def _send_update(self, message_type: UpdateMessageType, data: Any):
        """Send an update message to all registered update handlers."""
        for handler in self._on_update_handlers:
            handler(message_type, data)
