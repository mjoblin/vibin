import inspect
import json
from typing import Callable, List, Optional

import upnpclient
from upnpclient.soap import SOAPError

from vibin import VibinError
import vibin.mediasources as mediasources
from vibin.mediasources import MediaSource
from vibin.models import Album, Track
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
    ):
        logger.info("Initializing Vibin")

        self._current_streamer: Optional[Streamer] = None
        self._current_media_source: Optional[MediaSource] = None

        # Callables that want to be called (with all current state vars as
        # stringified JSON) whenever the state vars are updated.
        self._on_state_vars_update_handlers: List[Callable[[str], None]] = []

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

    def repeat(self, enabled: Optional[bool]):
        try:
            self.streamer.repeat(enabled)
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to interact with Repeat setting: [{code}] {err}"
            )

    def shuffle(self, enabled: Optional[bool]):
        try:
            self.streamer.shuffle(enabled)
        except SOAPError as e:
            code, err = e.args
            raise VibinError(
                f"Unable to interact with Shuffle setting: [{code}] {err}"
            )

    def seek(self, target):
        self.streamer.seek(target)

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
        all_vars = {
            self.streamer.name: self.streamer.state_vars,
            "vibin": {
                "last_played_id": self._last_played_id,
                self.streamer.name: self.streamer.vibin_vars
            }
        }

        return all_vars

    def on_state_vars_update(self, handler):
        self._on_state_vars_update_handlers.append(handler)

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

    def shutdown(self):
        logger.info("Vibin is shutting down")

        if self._current_streamer:
            logger.info(f"Disconnecting from {self._current_streamer.name}")
            self._current_streamer.disconnect()

        logger.info("Vibin shutdown complete")
