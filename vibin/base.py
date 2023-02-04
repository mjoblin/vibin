import concurrent.futures
import inspect
import json
import os
import re
from typing import Callable, List, Optional

import lyricsgenius
import upnpclient
from upnpclient.soap import SOAPError
import xml
import xmltodict

from vibin import VibinError, __version__
import vibin.external_services as external_services
from vibin.external_services import ExternalService
import vibin.mediasources as mediasources
from vibin.mediasources import MediaSource
from vibin.models import Album, ExternalServiceLink, Track
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

        self._last_played_id = None

        # Configure external services
        self._external_services: dict[str, ExternalService] = {}

        self._add_external_service(external_services.Discogs, "DISCOGS_ACCESS_TOKEN")
        self._add_external_service(external_services.Genius, "GENIUS_ACCESS_TOKEN")
        self._add_external_service(external_services.Wikipedia)

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

    def media_links(
            self,
            media_id: str,
            include_all: bool = False,
    ) -> dict[ExternalService.name, list[ExternalServiceLink]]:
        if len(self._external_services) == 0:
            return {}

        artist = album = title = media_class = None
        results = {}

        # TODO: Have errors raise an exception which can be passed back to the
        #   caller, rather than empty {} results.

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

    def modify_playlist(
            self,
            id: str,
            action:
            str = "REPLACE",
            insert_index: Optional[int] = None,
    ):
        self.streamer.play_metadata(self.media.get_metadata(id), action, insert_index)

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

    def transport_actions(self):
        return self.streamer.transport_actions()

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
        return {
            "streamer": self.streamer.system_state,
        }

    @property
    def play_state(self):
        return self.streamer.play_state

    # TODO: Fix handling of state_vars (UPNP) and updates (Websocket) to be
    #   more consistent. One option: more clearly configure handling of UPNP
    #   subscriptions and Websocket events from the streamer; both can be
    #   passed back to the client on the same Vibin->Client websocket
    #   connection, perhaps with different message type identifiers.

    def lyrics_for_track(self, track_id):
        if "Genius" not in self._external_services.keys():
            return

        try:
            track_info = xmltodict.parse(self.media.get_metadata(track_id))

            artist = track_info["DIDL-Lite"]["item"]["dc:creator"]
            title = track_info["DIDL-Lite"]["item"]["dc:title"]

            return self._external_services["Genius"].lyrics(artist, title)
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
        for handler in self._on_websocket_update_handlers:
            handler(message_type, data)

    def shutdown(self):
        logger.info("Vibin is shutting down")

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.disconnect()

        logger.info("Vibin shutdown complete")
