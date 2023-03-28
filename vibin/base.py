import concurrent.futures
import dataclasses
from dataclasses import asdict
import uuid
from functools import lru_cache
import inspect
import json
import operator
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, List, Optional

import requests
from tinydb import TinyDB, Query
import upnpclient
from upnpclient.soap import SOAPError
import xml
import xmltodict

from vibin import (
    VibinError,
    VibinNotFoundError,
    VibinMissingDependencyError,
    __version__,
)
from vibin.constants import APP_ROOT
import vibin.external_services as external_services
from vibin.external_services import ExternalService
import vibin.mediasources as mediasources
from vibin.mediasources import MediaSource
from vibin.models import (
    Album,
    ExternalServiceLink,
    Favorite,
    Links,
    Lyrics,
    Preset,
    StoredPlaylist,
    Track,
)
import vibin.streamers as streamers
from vibin.streamers import Streamer
from .logger import logger


class Vibin:
    def __init__(
            self,
            streamer: Optional[str] = None,
            media: Optional[str] = None,
            discovery_timeout: int = 5,
            subscribe_callback_base: Optional[str] = None,
            on_streamer_websocket_update=None,
    ):
        logger.info("Initializing Vibin")

        self._current_streamer: Optional[Streamer] = None
        self._current_media_source: Optional[MediaSource] = None

        # Callables that want to be called (with all current state vars as
        # stringified JSON) whenever the state vars are updated.
        self._on_state_vars_update_handlers: List[Callable[[str], None]] = []

        # TODO: Improve this hacked-in support for websocket updates.
        self._on_websocket_update_handlers: List[Callable[[str, str], None]] = []

        self._last_played_id = None

        # Configure external services
        self._external_services: dict[str, ExternalService] = {}

        self._add_external_service(external_services.Discogs, "DISCOGS_ACCESS_TOKEN")
        self._add_external_service(external_services.Genius, "GENIUS_ACCESS_TOKEN")
        self._add_external_service(external_services.RateYourMusic)
        self._add_external_service(external_services.Wikipedia)

        self._init_db()

        # Discover devices
        logger.info("Discovering devices...")
        devices = upnpclient.discover(timeout=discovery_timeout)

        for device in devices:
            logger.info(
                f'Found: {device.model_name} ("{device.friendly_name}")'
            )

        self._determine_streamer(devices, streamer, subscribe_callback_base)
        self._determine_media_source(devices, media)

        self._current_streamer.register_media_source(
            self._current_media_source
        )

        self._check_for_active_playlist_in_store()

    def _init_db(self):
        # Configure app-level persistent data directory.
        self._data_dir = Path(APP_ROOT, "_data")

        try:
            os.makedirs(self._data_dir, exist_ok=True)
        except OSError:
            raise VibinError(f"Cannot create data directory: {self._data_dir}")

        # Configure data store.
        self._db_file = Path(self._data_dir, "db.json")
        self._db = TinyDB(self._db_file)
        self._playlists = self._db.table("playlists")
        self._active_stored_playlist_id = None
        self._active_playlist_synced_with_store = False
        self._activating_stored_playlist = False

        self._favorites = self._db.table("favorites")
        self._lyrics = self._db.table("lyrics")
        self._links = self._db.table("links")

    def _check_for_active_playlist_in_store(
            self, call_handler_on_sync_loss=True, no_active_if_not_found=False
    ):
        # See if the current streamer playlist matches a stored playlist
        streamer_playlist = self.streamer.playlist(call_handler_on_sync_loss)

        if len(streamer_playlist) <= 0:
            self._active_stored_playlist_id = None
            self._active_playlist_synced_with_store = False
            self._send_stored_playlists_update()

            return

        # See if there's a stored playlist which matches the currently-active
        # streamer playlist (same media ids in the same order). If there's more
        # than one, then pick the one most recently updated.
        active_playlist_media_ids = [
            entry["trackMediaId"] for entry in streamer_playlist
        ]

        stored_playlists_as_dicts = [
            StoredPlaylist(**p) for p in self._playlists.all()
        ]

        try:
            stored_playlist_matching_active = sorted([
                playlist for playlist in stored_playlists_as_dicts
                if playlist.entry_ids == active_playlist_media_ids
            ], key=operator.attrgetter("updated"), reverse=True)[0]

            self._active_stored_playlist_id = stored_playlist_matching_active.id
            self._active_playlist_synced_with_store = True
        except IndexError:
            self._active_playlist_synced_with_store = False

            if no_active_if_not_found:
                self._active_stored_playlist_id = None

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

    # TODO: Do we want this
    def artist_links(self, artist: str):
        pass

    # TODO: Centralize all the DIDL-parsing logic. It might be helpful to have
    #   one centralized way to provide some XML media info and extract all the
    #   useful information from it, in a Vibin-contract-friendly way (well-
    #   defined concepts for title, artist, album, track artist vs. album
    #   artist, composer, etc).
    def _artist_from_track_media_info(self, track):
        artist = None

        try:
            didl_item = track["DIDL-Lite"]["item"]

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

    def _send_stored_playlists_update(self):
        self._websocket_message_handler(
            "StoredPlaylists", json.dumps(self.stored_playlist_details)
        )

    def _send_favorites_update(self):
        self._websocket_message_handler(
            "Favorites", json.dumps(self.favorites())
        )

    def media_links(
            self,
            *,
            media_id: Optional[str] = None,
            artist: Optional[str] = None,
            album: Optional[str] = None,
            title: Optional[str] = None,
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
                media_info = xmltodict.parse(self.media.get_metadata(media_id))
                didl = media_info["DIDL-Lite"]

                if "container" in didl:
                    # Album
                    artist = didl["container"]["dc:creator"]
                    album = didl["container"]["dc:title"]
                elif "item" in didl:
                    # Track
                    artist = self._artist_from_track_media_info(media_info)
                    album = didl["item"]["upnp:album"]
                    title = didl["item"]["dc:title"]
                else:
                    logger.error(
                        f"Could not determine whether media item is an Album or " +
                        f"a Track: {media_id}"
                    )
                    return {}
            except xml.parsers.expat.ExpatError as e:
                logger.error(
                    f"Could not convert XML to JSON for media item: {media_id}: {e}"
                )
                return {}
            except KeyError as e:
                logger.error(
                    f"Could not find expected media key in {media_id}: {e}"
                )
                return {}

        try:
            link_type = \
                "All" if include_all else ("Album" if not title else "Track")

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_link_getters = {
                    executor.submit(
                        service.links,
                        **{
                            "artist": artist,
                            "album": album,
                            "track": title,
                            "link_type": link_type,
                        }
                    ): service for service in self._external_services.values()
                }

                for future in concurrent.futures.as_completed(
                        future_to_link_getters
                ):
                    link_getter = future_to_link_getters[future]

                    try:
                        results[link_getter.name] = future.result()
                    except Exception as exc:
                        logger.error(
                            f"Could not retrieve links from " +
                            f"{link_getter.name}: {exc}"
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

            self._links.insert(link_data.dict())

        return results

    def _determine_streamer(
            self, devices, streamer_name, subscribe_callback_base
    ):
        # Build a map (device model name to Streamer subclass) of all the
        # streamers Vibin is able to handle.
        known_streamers_by_model: dict[str, Streamer] = {}

        for name, obj in inspect.getmembers(streamers):
            if inspect.isclass(obj) and issubclass(obj, Streamer):
                known_streamers_by_model[obj.model_name] = obj

        # Build a list of streamer devices that Vibin can handle.
        streamer_devices: list[upnpclient.Device] = [
            device
            for device in devices
            if device.model_name in known_streamers_by_model
        ]

        streamer_device = None  # The streamer device we want to end up using.

        if streamer_name:
            # Caller provided a streamer name to match against. We match against
            # the device friendly names.
            streamer_device = next(
                (
                    device for device in streamer_devices
                    if device.friendly_name == streamer_name
                ), None
            )
        elif len(streamer_devices) > 0:
            # Fall back on the first streamer.
            streamer_device = streamer_devices[0]

        if not streamer_device:
            # No streamer is considered unrecoverable.
            msg = (
                f'Could not find streamer "{streamer_name}"' if streamer_name
                else "Could not find any known streamer devices"
            )
            raise VibinError(msg)

        # Create an instance of the Streamer subclass which we can use to
        # manage our streamer device.
        streamer_class = known_streamers_by_model[streamer_device.model_name]
        self._current_streamer = streamer_class(
            device=streamer_device,
            subscribe_callback_base=subscribe_callback_base,
            updates_handler=self._websocket_message_handler,
            on_playlist_modified=self._on_playlist_modified,
        )

        logger.info(f'Using streamer: "{self._current_streamer.name}"')

    def _determine_media_source(self, devices, media_name):
        # Build a map (device model name to MediaSource subclass) of all the
        # media sources Vibin is able to handle.
        known_media_by_model: dict[str, MediaSource] = {}

        for name, obj in inspect.getmembers(mediasources):
            if inspect.isclass(obj) and issubclass(obj, MediaSource):
                known_media_by_model[obj.model_name] = obj

        # Build a list of media source devices that Vibin can handle.
        media_devices: list[upnpclient.Device] = [
            device
            for device in devices
            if device.model_name in known_media_by_model
        ]

        media_device = None  # The media source device we want to end up using.

        if media_name:
            media_device = next(
                (
                    device for device in media_devices
                    if device.friendly_name == media_name
                ), None
            )
        elif len(media_devices) > 0:
            # Fall back on the first media source.
            media_device = media_devices[0]

        if not media_device and media_name:
            # No media source when the user specified a media source name is
            # considered unrecoverable.
            raise VibinError(f"Could not find media source {media_name}")

        # Create an instance of the MediaSource subclass which we can use to
        # manage our media device.
        media_source_class = known_media_by_model[media_device.model_name]
        self._current_media_source = media_source_class(device=media_device)

        logger.info(f'Using media source: "{self._current_media_source.name}"')

    @property
    def streamer(self):
        return self._current_streamer

    @property
    def media(self):
        return self._current_media_source

    def browse_media(self, parent_id: str = "0"):
        return self.media.children(parent_id)

    def play_album(self, album: Album):
        self.play_id(album.id)

    def play_track(self, track: Track):
        self.play_id(track.id)

    def play_id(self, id: str):
        self.streamer.play_metadata(self.media.get_metadata(id))
        self._last_played_id = id

    def play_ids(self, media_ids):
        self.streamer.playlist_clear()

        for media_id in media_ids:
            self.modify_playlist(media_id, "APPEND")

        if len(media_ids) > 0:
            self.streamer.play_playlist_index(0)
            self._last_played_id = media_ids[0]
        else:
            self._last_played_id = None

    def play_favorite_albums(self):
        self.streamer.playlist_clear()

        for album in self.favorites(["album"]):
            self.modify_playlist(album["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    def play_favorite_tracks(self):
        self.streamer.playlist_clear()

        for track in self.favorites(["track"]):
            self.modify_playlist(track["media_id"], "APPEND")

        self.streamer.play_playlist_index(0)

    def modify_playlist(
            self,
            id: str,
            action: str = "REPLACE",
            insert_index: Optional[int] = None,
    ):
        self.streamer.play_metadata(self.media.get_metadata(id), action, insert_index)

        if action == "REPLACE":
            self._check_for_active_playlist_in_store(no_active_if_not_found=True)

    def pause(self):
        try:
            self.streamer.pause()
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to perform Pause transition: [{code}] {err}"
            )

    def play(self):
        try:
            self.streamer.play()
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to perform Play transition: [{code}] {err}"
            )

    def next_track(self):
        try:
            self.streamer.next_track()
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to perform Next transition: [{code}] {err}"
            )

    def previous_track(self):
        try:
            self.streamer.previous_track()
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to perform Previous transition: [{code}] {err}"
            )

    def repeat(self, state: Optional[str] = "toggle"):
        try:
            self.streamer.repeat(state)
        except SOAPError as e:
            # TODO: Will no longer get a SOAPError after switching to SMOIP
            code, err = e.args
            raise VibinError(
                f"Unable to interact with Repeat setting: [{code}] {err}"
            )

    def shuffle(self, state: Optional[str] = "toggle"):
        try:
            self.streamer.shuffle(state)
        except SOAPError as e:
            # TODO: Will no longer get a SOAPError after switching to SMOIP
            code, err = e.args
            raise VibinError(
                f"Unable to interact with Shuffle setting: [{code}] {err}"
            )

    def seek(self, target):
        self.streamer.seek(target)

    def transport_position(self):
        return self.streamer.transport_position()

    # TODO: Deprecate
    def transport_actions(self):
        return self.streamer.transport_actions()

    def transport_active_controls(self):
        return self.streamer.transport_active_controls()

    def transport_state(self) -> streamers.TransportState:
        return self.streamer.transport_state()

    def transport_status(self) -> str:
        return self.streamer.transport_status()

    # TODO: Consider improving this eventing system. Currently it only allows
    #   the streamer to subscribe to events; and when a new event comes in,
    #   it checks the event's service name against all the streamers
    #   subscriptions. It might be better to allow multiple streamer/media/etc
    #   objects to register event handlers with Vibin.

    def subscribe(self):
        self.streamer.subscribe()

    @property
    def state_vars(self):
        # TODO: Do a pass at redefining the shape of state_vars. It should
        #   include:
        #   * Standard keys shared across all streamers/media (audience: any
        #     client which wants to be device-agnostic). This will require some
        #     well-defined keys in some sort of device interface definition.
        #   * All streamer- and media-specific data (audience: any client which
        #     is OK with understanding device-specific data).
        #
        # TODO: Confusion: streamer_name/media_source_name vs. system_state()
        all_vars = {
            "streamer_name": self.streamer.name,
            "media_source_name": self.media.name,
            self.streamer.name: self.streamer.state_vars,
            "vibin": {
                "last_played_id": self._last_played_id,
                self.streamer.name: self.streamer.vibin_vars
            }
        }

        return all_vars

    @property
    def system_state(self):
        # TODO: Confusion: streamer_name/media_source_name vs. system_state()
        return {
            "streamer": self.streamer.system_state,
            "media": self.media.system_state,
        }

    @property
    def play_state(self):
        return self.streamer.play_state

    @property
    def device_display(self):
        return self.streamer.device_display

    @property
    def stored_playlist_details(self):
        return {
            "active_stored_playlist_id": self._active_stored_playlist_id,
            "active_synced_with_store": self._active_playlist_synced_with_store,
            "activating_stored_playlist": self._activating_stored_playlist,
            "stored_playlists": self._playlists.all(),
        }

    # TODO: Fix handling of state_vars (UPNP) and updates (Websocket) to be
    #   more consistent. One option: more clearly configure handling of UPNP
    #   subscriptions and Websocket events from the streamer; both can be
    #   passed back to the client on the same Vibin->Client websocket
    #   connection, perhaps with different message type identifiers.

    def lyrics_for_track(
            self, update_cache=False, *, track_id=None, artist=None, title=None
    ):
        if ("Genius" not in self._external_services.keys()) \
                or (track_id is None and (artist is None or title is None)):
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
                self._lyrics.remove(doc_ids=[stored_lyrics.doc_id])
            else:
                lyrics_data = Lyrics(**stored_lyrics)
                return lyrics_data

        if track_id:
            # Extract artist and title from the media metadata
            try:
                track_info = xmltodict.parse(self.media.get_metadata(track_id))

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

            self._lyrics.insert(lyric_data.dict())

            return lyric_data
        except VibinError as e:
            logger.error(e)

        return None

    def lyrics_valid(self, lyrics_id: str, *, is_valid: bool = True):
        StoredLyricsQuery = Query()
        stored_lyrics = self._lyrics.get(StoredLyricsQuery.lyrics_id == lyrics_id)

        if stored_lyrics is None:
            raise VibinNotFoundError(f"Could not find lyrics id: {lyrics_id}")

        self._lyrics.update(
            {"is_valid": is_valid}, doc_ids=[stored_lyrics.doc_id]
        )

    def lyrics_search(self, search_query: str):
        def matches_regex(values, pattern):
            return any(
                re.search(pattern, value, flags=re.IGNORECASE)
                for value in values
            )

        Lyrics = Query()
        Chunk = Query()

        results = self._lyrics.search(Lyrics.chunks.any(
            Chunk.header.search(search_query, flags=re.IGNORECASE) |
            Chunk.body.test(matches_regex, search_query))
        )

        # Only return stored lyrics which include a media id. This is because
        # we also store lyrics from sources like Airplay and don't want to
        # return those when doing a lyrics search (the search context is
        # intended to be local media only).

        return [
            result["media_id"]
            for result in results
            if result["media_id"] is not None
        ]

    # Expect data_format to be "json", "dat", or "png"
    # TODO: Investigate storing waveforms in a persistent cache/DB rather than
    #   relying on @lru_cache.
    @lru_cache
    def waveform_for_track(
            self, track_id, data_format="json", width=800, height=250
    ):
        try:
            track_info = xmltodict.parse(self.media.get_metadata(track_id))

            audio_files = [
                file for file in track_info["DIDL-Lite"]["item"]["res"]
                if file["#text"].endswith(".flac")
                or file["#text"].endswith(".wav")
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
                    ] +
                    (
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
                        ] if data_format == "png" else []
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
            logger.error(
                f"Could not convert XML to JSON for track: {track_id}: {e}"
            )
        except VibinError as e:
            logger.error(e)

        return None

    def on_state_vars_update(self, handler):
        self._on_state_vars_update_handlers.append(handler)

    # NOTE: Intended use: For an external entity to register interest in
    #   receiving websocket messages as they come in.
    def on_websocket_update(self, handler):
        self._on_websocket_update_handlers.append(handler)

    def upnp_event(self, service_name: str, event: str):
        # Extract the event.

        # Pass event to the streamer.
        if self.streamer.subscriptions:
            subscribed_service_names = [
                service.name for service in self.streamer.subscriptions.keys()
            ]

            if service_name in subscribed_service_names:
                self.streamer.on_event(service_name, event)

            # Send state vars to interested recipients.
            for handler in self._on_state_vars_update_handlers:
                handler(json.dumps(self.state_vars))

    def _websocket_message_handler(self, message_type: str, data: str):
        # TODO: This is passing raw CXNv2 payloads. The shape should be defined
        #   by the streamer contract and adhered to by cxnv2.py.
        for handler in self._on_websocket_update_handlers:
            handler(message_type, data)

    # TODO: Should _on_playlist_modified receive some information about the
    #   modification.
    def _on_playlist_modified(self):
        if self.streamer:
            self._check_for_active_playlist_in_store(
                call_handler_on_sync_loss=False
            )

    def shutdown(self):
        logger.info("Vibin is shutting down")

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.disconnect()

        logger.info("Vibin shutdown complete")

    def playlists(self) -> list[StoredPlaylist]:
        return self._playlists.all()

    def get_playlist(self, playlist_id) -> Optional[StoredPlaylist]:
        PlaylistQuery = Query()
        return self._playlists.get(PlaylistQuery.id == playlist_id)

    def set_current_playlist(self, playlist_id: str) -> StoredPlaylist:
        self._activating_stored_playlist = True
        self._send_stored_playlists_update()

        PlaylistQuery = Query()
        playlist = self._playlists.get(PlaylistQuery.id == playlist_id)

        if playlist is None:
            raise VibinNotFoundError()

        playlist_data = StoredPlaylist(**playlist)
        self.streamer.playlist_clear()

        self.streamer.ignore_playlist_updates(True)

        for entry_id in playlist_data.entry_ids:
            self.streamer.play_metadata(
                self.media.get_metadata(entry_id), action="APPEND"
            )

        self.streamer.ignore_playlist_updates(False)

        self._active_stored_playlist_id = playlist_id
        self._active_playlist_synced_with_store = True
        self._activating_stored_playlist = False

        self._send_stored_playlists_update()

        return StoredPlaylist(**playlist)

    def store_current_playlist(
            self,
            metadata: Optional[dict[str, any]] = None,
            replace: bool = True,
    ) -> StoredPlaylist:
        current_playlist = self.streamer.playlist()
        now = time.time()
        new_playlist_id = str(uuid.uuid4())

        if self._active_stored_playlist_id is None or replace is False:
            playlist_data = StoredPlaylist(
                id=new_playlist_id,
                name=metadata["name"] if metadata and "name" in metadata else "Unnamed",
                created=now,
                updated=now,
                entry_ids=[entry["trackMediaId"] for entry in current_playlist],
            )

            self._playlists.insert(asdict(playlist_data))
            self._active_stored_playlist_id = new_playlist_id
        else:
            updates = {
                "updated": now,
                "entry_ids": [
                    entry["trackMediaId"] for entry in current_playlist
                ],
            }

            if metadata and "name" in metadata:
                updates["name"] = metadata["name"]

            PlaylistQuery = Query()

            try:
                doc_id = self._playlists.update(
                    updates, PlaylistQuery.id == self._active_stored_playlist_id
                )[0]

                playlist_data = StoredPlaylist(**self._playlists.get(doc_id=doc_id))
            except IndexError:
                raise VibinError(
                    f"Could not update Playlist Id: {self._active_stored_playlist_id}"
                )

        self._active_playlist_synced_with_store = True
        self._send_stored_playlists_update()

        return playlist_data

    def delete_playlist(self, playlist_id: str):
        PlaylistQuery = Query()
        playlist_to_delete = self._playlists.get(PlaylistQuery.id == playlist_id)

        if playlist_to_delete is None:
            raise VibinNotFoundError()

        self._playlists.remove(doc_ids=[playlist_to_delete.doc_id])
        self._send_stored_playlists_update()

    def update_playlist_metadata(
            self, playlist_id: str, metadata: dict[str, any]
    ) -> StoredPlaylist:
        now = time.time()
        PlaylistQuery = Query()

        try:
            updated_ids = self._playlists.update(
                {
                    "updated": now,
                    "name": metadata["name"],
                },
                PlaylistQuery.id == playlist_id
            )

            if updated_ids is None or len(updated_ids) <= 0:
                raise VibinNotFoundError()

            self._send_stored_playlists_update()

            return StoredPlaylist(**self._playlists.get(doc_id=updated_ids[0]))
        except IndexError:
            raise VibinError(
                f"Could not update Playlist Id: {playlist_id}"
            )

    def favorites(
            self,
            requested_types: Optional[list[str]] = None
    ) -> list[dict[str, Album | Track]]:
        media_hydrators = {
            "album": self.media.album,
            # "artist": self.media.artist,
            "track": self.media.track,
        }

        return [
            {
                "type": favorite["type"],
                "media_id": favorite["media_id"],
                "when_favorited": favorite["when_favorited"],
                "media": dataclasses.asdict(
                    media_hydrators[favorite["type"]](favorite["media_id"])
                ),
            }
            for favorite in self._favorites.all()
            if requested_types is None or favorite["type"] in requested_types
        ]

    def store_favorite(self, favorite_type: str, media_id: str):
        # Check for existing favorite with this media_id
        FavoritesQuery = Query()
        existing_favorite = self._favorites.get(FavoritesQuery.media_id == media_id)

        if existing_favorite:
            return

        # Check that favorite media_id exists
        media_hydrators = {
            "album": self.media.album,
            # "artist": self.media.artist,
            "track": self.media.track,
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

        self._favorites.insert(favorite_data.dict())
        self._send_favorites_update()

        return favorite_data

    def delete_favorite(self, media_id: str):
        FavoritesQuery = Query()
        favorite_to_delete = self._favorites.get(FavoritesQuery.media_id == media_id)

        if favorite_to_delete is None:
            raise VibinNotFoundError()

        self._favorites.remove(doc_ids=[favorite_to_delete.doc_id])
        self._send_favorites_update()

    @property
    def presets(self):
        return self.streamer.presets

    def db_get(self):
        with open(self._db_file, "r") as fh:
            return json.loads(fh.read())

    def db_set(self, data):
        with open(self._db_file, "w") as fh:
            fh.write(json.dumps(data))

        self._init_db()
