import concurrent.futures
import uuid
import functools
from functools import lru_cache
import json
import operator
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable

import requests
from tinydb import TinyDB, Query
from tinyrecord import transaction
import xml
import xmltodict

from vibin import (
    VibinError,
    VibinNotFoundError,
    VibinMissingDependencyError,
    __version__,
)
from vibin.constants import (
    DB_ROOT,
    DEFAULT_ALL_ALBUMS_PATH,
    DEFAULT_ALL_ARTISTS_PATH,
    DEFAULT_NEW_ALBUMS_PATH,
)
import vibin.external_services as external_services
from vibin.external_services import ExternalService
from vibin.mediaservers import MediaServer
from vibin.models import (
    ActivePlaylistEntry,
    Album,
    CurrentlyPlaying,
    ExternalServiceLink,
    Favorite,
    FavoritesPayload,
    Links,
    Lyrics,
    MediaBrowseSingleLevel,
    Presets,
    UPnPServiceSubscriptions,
    StoredPlaylist,
    StoredPlaylists,
    StoredPlaylistStatus,
    SystemState,
    Track,
    TransportPlayState,
    UpdateMessage,
    VibinSettings,
)
from .types import UpdateMessageHandler, UPnPDeviceType, UpdateMessageType
from vibin.streamers import Streamer
from .device_resolution import (
    determine_media_server_class,
    determine_streamer_and_media_server,
    determine_streamer_class,
)
from .logger import logger


def requires_media(return_val=None):
    def decorator_requires_media(func):
        @functools.wraps(func)
        def wrapper_requires_media(*args, **kwargs):
            if args[0].media_server is not None:
                return func(*args, **kwargs)
            else:
                return return_val

        return wrapper_requires_media

    return decorator_requires_media


class Vibin:
    def __init__(
        self,
        streamer: str | None = None,
        streamer_type: str | None = None,
        media_server: str | bool | None = None,
        media_server_type: str | None = None,
        discovery_timeout: int = 5,
        subscribe_callback_base: str | None = None,  # TODO: Rename upnp_subscription_callback_base
    ):
        logger.info("Initializing Vibin")

        self._on_update_handlers: list[UpdateMessageHandler] = []
        self._last_played_id = None

        # Configure external services
        self._external_services: dict[str, ExternalService] = {}

        self._add_external_service(external_services.Discogs, "DISCOGS_ACCESS_TOKEN")
        self._add_external_service(external_services.Genius, "GENIUS_ACCESS_TOKEN")
        self._add_external_service(external_services.RateYourMusic)
        self._add_external_service(external_services.Wikipedia)

        self._stored_playlist_status = StoredPlaylistStatus()
        self._ignore_playlist_updates = False
        self._cached_stored_playlist: StoredPlaylist | None = None
        self._init_db()

        # Set up the streamer and media server instances

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
            subscribe_callback_base=f"{subscribe_callback_base}/streamer",
            on_update=self._on_streamer_update,
            on_playlist_modified=self._on_playlist_modified,
        )

        logger.info(
            f"Using streamer UPnP device: {self.streamer.name} ({type(self.streamer).__name__})"
        )

        # Media server

        if media_server_device:
            media_server_class = determine_media_server_class(
                media_server_device, media_server_type
            )

            # Create an instance of the MediaServer subclass which we can use to
            # manage our media device.
            self._current_media_server = media_server_class(
                device=media_server_device,
                subscribe_callback_base=f"{subscribe_callback_base}/media_server",
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

        self._check_for_active_playlist_in_store()
        self._subscribe_to_upnp_events()

    def __str__(self):
        return (
            f"Vibin: "
            + f"streamer:'{'None' if self.streamer is None else self.streamer.name}'; "
            + f"media server:'{'None' if self.media_server is None else self.media_server.name}'"
        )

    def get_current_state_messages(self) -> list[UpdateMessage]:
        return [
            UpdateMessage(message_type="System", payload=self.system_state),
            UpdateMessage(message_type="UPnPProperties", payload=self.upnp_properties),
            UpdateMessage(message_type="PlayState", payload=self.play_state),
            UpdateMessage(
                message_type="TransportState", payload=self.streamer.transport_state
            ),
            UpdateMessage(message_type="CurrentlyPlaying", payload=self.currently_playing),
            UpdateMessage(
                message_type="DeviceDisplay", payload=self.streamer.device_display
            ),
            UpdateMessage(
                message_type="Favorites", payload=FavoritesPayload(favorites=self.favorites)
            ),
            UpdateMessage(message_type="Presets", payload=self.presets),
            UpdateMessage(message_type="StoredPlaylists", payload=self.stored_playlists),
            UpdateMessage(
                message_type="ActiveTransportControls",
                payload=self.streamer.active_transport_controls,
            ),
        ]

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

    def _init_db(self):
        # Configure app-level persistent data directory.
        try:
            os.makedirs(DB_ROOT, exist_ok=True)
        except OSError:
            raise VibinError(f"Cannot create data directory: {DB_ROOT}")

        # Configure data store.
        self._db_file = Path(DB_ROOT, "db.json")
        self._db = TinyDB(self._db_file)
        self._settings_table = self._db.table("settings")
        self._playlists = self._db.table("playlists")
        self._favorites = self._db.table("favorites")
        self._lyrics = self._db.table("lyrics")
        self._links = self._db.table("links")

        if len(self._settings_table.all()) == 0:
            settings = VibinSettings(
                all_albums_path=DEFAULT_ALL_ALBUMS_PATH,
                new_albums_path=DEFAULT_NEW_ALBUMS_PATH,
                all_artists_path=DEFAULT_ALL_ARTISTS_PATH,
            )

            with transaction(self._settings_table) as tr:
                tr.insert(settings.dict())

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

        stored_playlists_as_dicts = [StoredPlaylist(**p) for p in self._playlists.all()]

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

    def _add_external_service(self, service_class, token_env_var=None):
        try:
            service_instance = service_class(
                # TODO: Change user agent to Vibin
                user_agent=f"ExampleApplication/{__version__}",
                token=os.environ[token_env_var] if token_env_var else None,
            )

            self._external_services[service_instance.name] = service_instance

            logger.info(f"Registered external service: {service_instance.name}")
        except KeyError:
            pass

    @property
    def settings(self):
        return VibinSettings.parse_obj(self._settings_table.all()[0])

    @settings.setter
    def settings(self, settings: VibinSettings):
        with transaction(self._settings_table) as tr:
            tr.update(settings.dict())

        self._current_media_server.all_albums_path = settings.all_albums_path
        self._current_media_server.new_albums_path = settings.new_albums_path
        self._current_media_server.all_artists_path = settings.all_artists_path

    # TODO: Do we want this
    def artist_links(self, artist: str):
        pass

    # TODO: Centralize all the DIDL-parsing logic. It might be helpful to have
    #   one centralized way to provide some XML media info and extract all the
    #   useful information from it, in a Vibin-contract-friendly way (well-
    #   defined concepts for title, artist, album, track artist vs. album
    #   artist, composer, etc).
    @requires_media()
    def _artist_name_from_track_media_info(self, track_info) -> str:
        artist = None

        try:
            didl_item = track_info["DIDL-Lite"]["item"]

            # Default to dc:creator
            artist = didl_item["dc:creator"]

            # Attempt to find AlbumArtist in upnp:artist
            upnp_artist_info = didl_item["upnp:artist"]

            if type(upnp_artist_info) == str:
                artist = upnp_artist_info
            else:
                # We have an array of artists, so look for AlbumArtist (others
                # might be Composer, etc).
                for upnp_artist in upnp_artist_info:
                    if upnp_artist["@role"] == "AlbumArtist":
                        artist = upnp_artist["#text"]
                        break
        except KeyError:
            pass

        return artist

    @requires_media()
    def _send_stored_playlists_update(self):
        self._send_update("StoredPlaylists", self.stored_playlists)

    @requires_media()
    def _send_favorites_update(self):
        self._send_update("Favorites", FavoritesPayload(favorites=self.favorites))

    @requires_media()
    def media_links(
        self,
        *,
        media_id: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        title: str | None = None,
        include_all: bool = False,
    ) -> dict[ExternalService.name, list[ExternalServiceLink]]:
        if len(self._external_services) == 0:
            return {}

        # Check if links are already stored
        if media_id:
            StoredLinksQuery = Query()
            stored_links = self._links.get(StoredLinksQuery.media_id == media_id)

            if stored_links is not None:
                links_data = Links(**stored_links)
                return links_data.links

        results = {}

        # TODO: Have errors raise an exception which can be passed back to the
        #   caller, rather than empty {} results.

        if media_id:
            try:
                media_info = xmltodict.parse(self.media_server.get_metadata(media_id))
                didl = media_info["DIDL-Lite"]

                if "container" in didl:
                    # Album
                    artist = didl["container"]["dc:creator"]
                    album = didl["container"]["dc:title"]
                elif "item" in didl:
                    # Track
                    artist = self._artist_name_from_track_media_info(media_info)
                    album = didl["item"]["upnp:album"]
                    title = didl["item"]["dc:title"]
                else:
                    logger.error(
                        f"Could not determine whether media item is an Album or "
                        + f"a Track: {media_id}"
                    )
                    return {}
            except xml.parsers.expat.ExpatError as e:
                logger.error(
                    f"Could not convert XML to JSON for media item: {media_id}: {e}"
                )
                return {}
            except KeyError as e:
                logger.error(f"Could not find expected media key in {media_id}: {e}")
                return {}

        try:
            link_type = "All" if include_all else ("Album" if not title else "Track")

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_link_getters = {
                    executor.submit(
                        service.links,
                        **{
                            "artist": artist,
                            "album": album,
                            "track": title,
                            "link_type": link_type,
                        },
                    ): service
                    for service in self._external_services.values()
                }

                for future in concurrent.futures.as_completed(future_to_link_getters):
                    link_getter = future_to_link_getters[future]

                    try:
                        results[link_getter.name] = future.result()
                    except Exception as exc:
                        logger.error(
                            f"Could not retrieve links from "
                            + f"{link_getter.name}: {exc}"
                        )
        except xml.parsers.expat.ExpatError as e:
            logger.error(
                f"Could not convert XML to JSON for media item: {media_id}: {e}"
            )

        if media_id:
            # Persist to local data store.
            link_data = Links(
                media_id=media_id,
                links=results,
            )

            with transaction(self._links) as tr:
                tr.insert(link_data.dict())

        return results

    @property
    def streamer(self):
        return self._current_streamer

    @property
    def media_server(self):
        return self._current_media_server

    @requires_media()
    def browse_media(self, parent_id: str = "0") -> MediaBrowseSingleLevel:
        return self.media_server.children(parent_id)

    @requires_media()
    def play_album(self, album: Album):
        self.play_id(album.id)

    @requires_media()
    def play_track(self, track: Track):
        self.play_id(track.id)

    @requires_media()
    def play_id(self, id: str):
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.modify_playlist(self.media_server.get_metadata(id))
        self._last_played_id = id

    @requires_media()
    def play_ids(self, media_ids, max_count: int = 10):
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

    @requires_media()
    def play_favorite_albums(self, max_count: int = 10):
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.playlist_clear()

        # TODO: Consider adding a hard max_count limit
        for album in self.favorite_albums[:max_count]:
            self.modify_playlist(album["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    @requires_media()
    def play_favorite_tracks(self, max_count: int = 100):
        self._reset_stored_playlist_status(send_update=True)
        self.streamer.playlist_clear()

        # TODO: Consider adding a hard max_count limit
        for track in self.favorite_tracks[:max_count]:
            self.modify_playlist(track["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    @requires_media()
    def modify_playlist(
        self,
        id: str,
        action: str = "REPLACE",
        insert_index: int | None = None,
    ):
        self.streamer.modify_playlist(self.media_server.get_metadata(id), action, insert_index)

        if action == "REPLACE":
            self._reset_stored_playlist_status(send_update=True)

    # def pause(self):
    #     try:
    #         self.streamer.pause()
    #     except SOAPError as e:
    #         code, err = e.args
    #         raise VibinError(f"Unable to perform Pause transition: [{code}] {err}")
    #
    # def play(self):
    #     try:
    #         self.streamer.play()
    #     except SOAPError as e:
    #         code, err = e.args
    #         raise VibinError(f"Unable to perform Play transition: [{code}] {err}")
    #
    # def next_track(self):
    #     try:
    #         self.streamer.next_track()
    #     except SOAPError as e:
    #         code, err = e.args
    #         raise VibinError(f"Unable to perform Next transition: [{code}] {err}")
    #
    # def previous_track(self):
    #     try:
    #         self.streamer.previous_track()
    #     except SOAPError as e:
    #         code, err = e.args
    #         raise VibinError(f"Unable to perform Previous transition: [{code}] {err}")

    # def repeat(self, state: str | None = "toggle"):
    #     try:
    #         self.streamer.repeat(state)
    #     except SOAPError as e:
    #         # TODO: Will no longer get a SOAPError after switching to SMOIP
    #         code, err = e.args
    #         raise VibinError(f"Unable to interact with Repeat setting: [{code}] {err}")
    #
    # def shuffle(self, state: str | None = "toggle"):
    #     try:
    #         self.streamer.shuffle(state)
    #     except SOAPError as e:
    #         # TODO: Will no longer get a SOAPError after switching to SMOIP
    #         code, err = e.args
    #         raise VibinError(f"Unable to interact with Shuffle setting: [{code}] {err}")

    # def seek(self, target):
    #     self.streamer.seek(target)

    # def transport_position(self):
    #     return self.streamer.transport_position()

    # def transport_active_controls(self):
    #     return self.streamer.transport_active_controls()

    # @property
    # def transport_state(self) -> TransportState:
    #     return self.streamer.transport_state

    # TODO: Consider improving this eventing system. Currently it only allows
    #   the streamer to subscribe to events; and when a new event comes in,
    #   it checks the event's service name against all the streamers
    #   subscriptions. It might be better to allow multiple streamer/media/etc
    #   objects to register event handlers with Vibin.

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

    @property
    def upnp_properties(self):
    # def upnp_properties(self) -> SystemUPnPProperties:
        # return SystemUPnPProperties(
        #     streamer=self.streamer.upnp_properties,
        #     media_server=self.media.upnp_properties,
        # )

        # TODO: Do a pass at redefining the shape of upnp_properties. It should
        #   include:
        #   * Standard keys shared across all streamers/media (audience: any
        #     client which wants to be device-agnostic). This will require some
        #     well-defined keys in some sort of device interface definition.
        #   * All streamer- and media-specific data (audience: any client which
        #     is OK with understanding device-specific data).
        #
        # TODO: Confusion: streamer_name/media_source_name vs. system_state()
        # TODO: Remove data which isn't directly upnp_properties
        #
        # TODO: Remove everything except streamer and media_server once migrated
        #   to other messages. Then use SystemUPnPProperties type.
        all_vars = {
            "streamer_name": self.streamer.name,
            "media_source_name": self.media_server.name if self.media_server else None,
            "streamer": self.streamer.upnp_properties,
            "media_server": self.media_server.upnp_properties if self.media_server else None,
            "vibin": {
                "last_played_id": self._last_played_id,
                self.streamer.name: self.streamer.vibin_vars,
            },
        }

        return all_vars

    @property
    def system_state(self) -> SystemState:
        return SystemState(
            streamer=self.streamer.device_state,
            media=self.media_server.device_state,
        )

    # TODO: Deprecate in favor of transport_state
    @property
    def play_state(self) -> TransportPlayState:
        return self.streamer.play_state

    @property
    def currently_playing(self) -> CurrentlyPlaying:
        return self.streamer.currently_playing

    @property
    def device_display(self):
        return self.streamer.device_display

    @property
    def stored_playlists(self) -> StoredPlaylists:
        return StoredPlaylists(
            status=self._stored_playlist_status,
            playlists=[StoredPlaylist(**playlist) for playlist in self._playlists.all()],
        )

        # return {
        #     "active_stored_playlist_id": self._stored_playlist_status.active_id,
        #     "active_synced_with_store": self._stored_playlist_status.is_active_synced_with_store,
        #     "activating_stored_playlist": self._stored_playlist_status.is_activating_new_playlist,
        #     "stored_playlists": self._playlists.all(),
        # }

    def lyrics_for_track(
        self, update_cache=False, *, track_id=None, artist=None, title=None
    ):
        if ("Genius" not in self._external_services.keys()) or (
            track_id is None and (artist is None or title is None)
        ):
            return

        def storage_id(track_id, artist, title) -> str:
            if track_id:
                return track_id

            return f"{artist}::{title}"

        # Check if lyrics are already stored
        StoredLyricsQuery = Query()
        stored_lyrics = self._lyrics.get(
            StoredLyricsQuery.lyrics_id == storage_id(track_id, artist, title)
        )

        if stored_lyrics is not None:
            if update_cache:
                with transaction(self._lyrics) as tr:
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
            lyric_chunks = self._external_services["Genius"].lyrics(artist, title)

            lyric_data = Lyrics(
                lyrics_id=storage_id(track_id, artist, title),
                media_id=track_id,
                is_valid=True,
                chunks=lyric_chunks if lyric_chunks is not None else [],
            )

            with transaction(self._lyrics) as tr:
                tr.insert(lyric_data.dict())

            return lyric_data
        except VibinError as e:
            logger.error(e)

        return None

    def lyrics_valid(self, lyrics_id: str, *, is_valid: bool = True):
        StoredLyricsQuery = Query()
        stored_lyrics = self._lyrics.get(StoredLyricsQuery.lyrics_id == lyrics_id)

        if stored_lyrics is None:
            raise VibinNotFoundError(f"Could not find lyrics id: {lyrics_id}")

        with transaction(self._lyrics) as tr:
            tr.update({"is_valid": is_valid}, doc_ids=[stored_lyrics.doc_id])

    def lyrics_search(self, search_query: str):
        def matches_regex(values, pattern):
            return any(
                re.search(pattern, value, flags=re.IGNORECASE) for value in values
            )

        Lyrics = Query()
        Chunk = Query()

        results = self._lyrics.search(
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

    # Expect data_format to be "json", "dat", or "png"
    # TODO: Investigate storing waveforms in a persistent cache/DB rather than
    #   relying on @lru_cache.
    @lru_cache
    @requires_media()
    def waveform_for_track(self, track_id, data_format="json", width=800, height=250):
        try:
            track_info = xmltodict.parse(self.media_server.get_metadata(track_id))

            audio_files = [
                file
                for file in track_info["DIDL-Lite"]["item"]["res"]
                if file["#text"].endswith(".flac") or file["#text"].endswith(".wav")
            ]

            audio_file = audio_files[0]["#text"]

            with tempfile.NamedTemporaryFile(
                prefix="vibin_", suffix=track_id
            ) as flac_file:
                with requests.get(audio_file, stream=True) as response:
                    shutil.copyfileobj(response.raw, flac_file)

                # Explanation for 8-bit data (--bits 8):
                # https://github.com/bbc/peaks.js#pre-computed-waveform-data

                waveform_data = subprocess.run(
                    [
                        "audiowaveform",
                        "--bits",
                        "8",
                        "--input-filename",
                        str(Path(tempfile.gettempdir(), str(flac_file.name))),
                        "--input-format",
                        Path(audio_file).suffix[1:],
                        "--output-format",
                        data_format,
                    ]
                    + (
                        [
                            "--zoom",
                            "auto",
                            "--width",
                            str(width),
                            "--height",
                            str(height),
                            "--colors",
                            "audition",
                            "--split-channels",
                            "--no-axis-labels",
                        ]
                        if data_format == "png"
                        else []
                    ),
                    capture_output=True,
                )

                if data_format == "json":
                    return json.loads(waveform_data.stdout.decode("utf-8"))
                else:
                    return waveform_data.stdout
        except FileNotFoundError:
            raise VibinMissingDependencyError("audiowaveform")
        except KeyError as e:
            raise VibinError(
                f"Could not find any file information for track: {track_id}"
            )
        except IndexError as e:
            raise VibinError(
                f"Could not find .flac or .wav file URL for track: {track_id}"
            )
        except xml.parsers.expat.ExpatError as e:
            logger.error(f"Could not convert XML to JSON for track: {track_id}: {e}")
        except VibinError as e:
            logger.error(e)

        return None

    # NOTE: Intended use: For an external entity to register interest in
    #   receiving websocket messages as they come in.
    # TODO: Rename to subscribe_to_updates() and handle way to ubsubscribe
    def on_update(self, handler: UpdateMessageHandler):
        self._on_update_handlers.append(handler)

    def on_upnp_event(self, device: UPnPDeviceType, service_name: str, event: str):
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

    def _on_streamer_update(self, message_type: UpdateMessageType, data: Any):
        # Forward streamer updates directly to all update subscribers.
        self._send_update(message_type, data)

    def _on_media_server_update(self, message_type: UpdateMessageType, data: Any):
        # Forward media server updates directly to all update subscribers.
        self._send_update(message_type, data)

    def _send_update(self, message_type: UpdateMessageType, data: Any):
        for handler in self._on_update_handlers:
            handler(message_type, data)

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

    def shutdown(self):
        logger.info("Vibin is shutting down")

        logger.info("Closing database")
        self._db.close()

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.on_shutdown()

        logger.info("Vibin shutdown complete")

    def playlists(self) -> list[StoredPlaylist]:
        return [StoredPlaylist(**playlist_dict) for playlist_dict in self._playlists.all()]

    def get_playlist(self, playlist_id) -> StoredPlaylist:
        PlaylistQuery = Query()
        playlist_dict = self._playlists.get(PlaylistQuery.id == playlist_id)

        if playlist_dict is None:
            raise VibinNotFoundError()

        return StoredPlaylist(**playlist_dict)

    @requires_media()
    def set_active_playlist(self, playlist_id: str) -> StoredPlaylist:
        self._reset_stored_playlist_status(is_activating=True, send_update=True)

        PlaylistQuery = Query()
        playlist_dict = self._playlists.get(PlaylistQuery.id == playlist_id)

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

    @requires_media()
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

            with transaction(self._playlists) as tr:
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
                with transaction(self._playlists) as tr:
                    doc_id = tr.update(
                        updates,
                        PlaylistQuery.id == self._stored_playlist_status.active_id,
                    )[0]

                playlist_data = StoredPlaylist(**self._playlists.get(doc_id=doc_id))
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

    @requires_media()
    def delete_playlist(self, playlist_id: str):
        PlaylistQuery = Query()
        playlist_to_delete = self._playlists.get(PlaylistQuery.id == playlist_id)

        if playlist_to_delete is None:
            raise VibinNotFoundError()

        with transaction(self._playlists) as tr:
            tr.remove(doc_ids=[playlist_to_delete.doc_id])

        self._send_stored_playlists_update()

    def update_playlist_metadata(
        self, playlist_id: str, metadata: dict[str, any]
    ) -> StoredPlaylist:
        now = time.time()
        PlaylistQuery = Query()

        try:
            with transaction(self._playlists) as tr:
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

            return StoredPlaylist(**self._playlists.get(doc_id=updated_ids[0]))
        except IndexError:
            raise VibinError(f"Could not update Playlist Id: {playlist_id}")

    @requires_media(return_val=[])
    def _favorites_getter(self, requested_types: list[str] | None = None) -> list[Favorite]:
        media_hydrators: dict[str, Callable[[str], Album | Track]] = {
            "album": self.media_server.album,
            # "artist": self.media.artist,
            "track": self.media_server.track,
        }

        return [
            Favorite(
                type=favorite["type"],
                media_id=favorite["media_id"],
                when_favorited=favorite["when_favorited"],
                media=media_hydrators[favorite["type"]](favorite["media_id"]),
            )
            for favorite in self._favorites.all()
            if requested_types is None or favorite["type"] in requested_types
        ]

    @property
    def favorites(self):
        return self._favorites_getter()

    @property
    def favorite_albums(self):
        return self._favorites_getter(requested_types=["album"])

    @property
    def favorite_tracks(self):
        return self._favorites_getter(requested_types=["track"])

    @requires_media()
    def store_favorite(self, favorite_type: str, media_id: str):
        # Check for existing favorite with this media_id
        FavoritesQuery = Query()
        existing_favorite = self._favorites.get(FavoritesQuery.media_id == media_id)

        if existing_favorite:
            return

        # Check that favorite media_id exists
        media_hydrators = {
            "album": self.media_server.album,
            # "artist": self.media.artist,
            "track": self.media_server.track,
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

        with transaction(self._favorites) as tr:
            tr.insert(favorite_data.dict())

        self._send_favorites_update()

        return favorite_data

    @requires_media()
    def delete_favorite(self, media_id: str):
        FavoritesQuery = Query()
        favorite_to_delete = self._favorites.get(FavoritesQuery.media_id == media_id)

        if favorite_to_delete is None:
            raise VibinNotFoundError()

        with transaction(self._favorites) as tr:
            tr.remove(doc_ids=[favorite_to_delete.doc_id])

        self._send_favorites_update()

    @property
    def presets(self) -> Presets:
        return self.streamer.presets

    def db_get(self):
        # NOTE: TinyDB isn't thread safe, and this code doesn't use tinyrecord,
        #   so it could in theory produce an incomplete result.
        with open(self._db_file, "r") as fh:
            return json.loads(fh.read())

    def db_set(self, data):
        # NOTE: TinyDB isn't thread safe, and this code doesn't use tinyrecord,
        #   so it could in theory corrupt the database.
        with open(self._db_file, "w") as fh:
            fh.write(json.dumps(data))

        self._init_db()
