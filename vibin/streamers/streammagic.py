import atexit
import json
import math
from typing import Literal, Any
from urllib.parse import urlparse, quote
import xml.etree.ElementTree as ET

from deepdiff import DeepDiff
import requests
import upnpclient
from websockets.legacy.client import WebSocketClientProtocol
from websockets.typing import Data

from vibin import (
    utils,
    VibinDeviceError,
    VibinError,
    VibinInputError,
    VibinNotFoundError,
)
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.models import (
    ActiveTrack,
    AudioSource,
    AudioSources,
    CurrentlyPlaying,
    MediaFormat,
    PlaylistModifyAction,
    PowerState,
    Presets,
    Queue,
    StreamerDeviceDisplay,
    StreamerState,
    TransportAction,
    TransportRepeatState,
    TransportShuffleState,
    TransportState,
    UPnPServiceSubscriptions,
)
from vibin.types import (
    MediaId,
    SeekTarget,
    TransportPosition,
    UpdateMessageHandler,
    UPnPProperties,
)
from vibin.streamers import Streamer
from vibin.utils import WebsocketThread


# See Streamer interface for method documentation.

# -----------------------------------------------------------------------------
# NOTE: Media IDs only make sense when streaming from a local source.
# -----------------------------------------------------------------------------

# TODO: This class would benefit from some overall refactoring. Its current
#  implementation isn't very clear and is showing its hacked-together history.
# TODO: Consider using httpx instead of requests, and making some of these
#   methods (particularly the ones that use SMOIP) async.


class StreamMagic(Streamer):
    model_name = "StreamMagic"

    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,  # Deprecated, unused
        on_update: UpdateMessageHandler | None = None,
    ):
        """Implement the Streamer interface for StreamMagic streamers."""
        self._device = device
        self._on_update = on_update

        self._device_hostname = urlparse(device.location).hostname

        self._device_state: StreamerState = StreamerState(
            name=self._device.friendly_name,
            power=None,
            sources=AudioSources(),
            display=StreamerDeviceDisplay(),
        )

        self._currently_playing: CurrentlyPlaying = CurrentlyPlaying()
        self._queue = Queue()
        self._transport_state: TransportState = TransportState()
        self._device_display_raw = {}

        self._disconnected = False
        self._media_server: MediaServer | None = None
        self._websocket_thread = None

        ET.register_namespace("", "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/")
        ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
        ET.register_namespace("upnp", "urn:schemas-upnp-org:metadata-1-0/upnp/")
        ET.register_namespace("dlna", "urn:schemas-dlna-org:metadata-1-0/")

        atexit.register(self.on_shutdown)

        # Determine audio sources
        try:
            response = requests.get(
                f"http://{self._device_hostname}/smoip/system/sources"
            )

            self._device_state.sources.available = [
                AudioSource(**source) for source in response.json()["data"]["sources"]
            ]
        except Exception:
            # TODO
            pass

        if self._on_update:
            self._websocket_thread = WebsocketThread(
                uri=f"ws://{self._device_hostname}:80/smoip",
                friendly_name=self._device.friendly_name,
                on_connect=self._initialize_websocket,
                on_data=self._process_streamer_message,
            )
            self._websocket_thread.start()

        # Keep track of last seen ("currently playing") track and album IDs.
        # This is done to facilitate injecting this information into payloads
        # which want it, but it isn't already there when sent from the streamer.
        self._set_last_seen_media_ids(None, None)

    @property
    def name(self):
        return self._device.friendly_name

    @property
    def device(self):
        return self._device

    @property
    def device_state(self) -> StreamerState:
        return self._device_state

    @property
    def device_udn(self):
        return self._device.udn.removeprefix("uuid:")

    @property
    def device_display(self) -> StreamerDeviceDisplay:
        return StreamerDeviceDisplay(**self._device_display_raw)

    def register_media_server(self, media_server: MediaServer):
        self._media_server = media_server

    def on_startup(self) -> None:
        # Perform any StreamMagic-related startup checks which need to wait
        # until the MediaServer and Amplifier have been initialized.

        # Figure out what the current queue looks like.
        self._set_queue()

    def on_shutdown(self) -> None:
        if self._disconnected:
            return

        logger.info("StreamMagic disconnect requested")
        self._disconnected = True

        if self._websocket_thread:
            logger.info(f"Stopping WebSocket thread for {self.name}")
            self._websocket_thread.stop()
            self._websocket_thread.join()

        logger.info("StreamMagic disconnection complete")

    # -------------------------------------------------------------------------
    # System

    @property
    def power(self) -> PowerState | None:
        return self._device_state.power

    @power.setter
    def power(self, state: PowerState) -> None:
        # If the streamer is already on, sending "ON" again seems to trigger a
        # reboot -- so only send an "ON" if the streamer is not already on.
        if state == "on" and self._device_state.power != "on":
            requests.get(f"http://{self._device_hostname}/smoip/system/power?power=ON")
        elif state == "off":
            requests.get(f"http://{self._device_hostname}/smoip/system/power?power=NETWORK")

    def power_toggle(self):
        requests.get(f"http://{self._device_hostname}/smoip/system/power?power=toggle")

    @property
    def currently_playing(self) -> CurrentlyPlaying:
        return self._currently_playing

    def set_audio_source(self, source_name: str):
        try:
            source_details = [
                source
                for source in self._device_state.sources.available
                if source.name == source_name
            ][0]

            requests.get(
                f"http://{self._device_hostname}/smoip/zone/state?source={source_details.id}"
            )
        except IndexError:
            raise VibinDeviceError(
                f"Could not find streamer source with name: {source_name}"
            )

    # -------------------------------------------------------------------------
    # Transport

    @property
    def transport_state(self) -> TransportState:
        return self._transport_state

    def play(self):
        if self._transport_state.play_state == "play":
            return

        if "toggle_playback" not in self._transport_state.active_controls:
            logger.warning("Play requested but toggle_playback not in active controls")
            return

        self.toggle_playback()

    def pause(self):
        if self._transport_state.play_state == "pause":
            return

        if "toggle_playback" not in self._transport_state.active_controls:
            logger.warning("Pause requested but toggle_playback not in active controls")
            return

        self.toggle_playback()

    def toggle_playback(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?action=toggle"
        )

    def stop(self):
        if "stop" not in self._transport_state.active_controls:
            logger.warning("Stop requested but stop not in active controls")
            return

        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?action=stop"
        )

    def seek(self, target: SeekTarget):
        if "seek" not in self.active_transport_controls:
            # TODO: Establish consistent way of handling "currently unavailable"
            #   features.
            raise VibinError("Seek currently unavailable")

        target_secs = None

        # TODO: Fix handling of float vs. int. All numbers come in as floats,
        #   so what to do with 1/1.0 is ambiguous (here we treat is as 1 second
        #   not the end of the 0-1 normalized range).
        if isinstance(target, float):
            if target == 0:
                target_secs = 0
            elif target < 1:
                # Normalized seek (0.0 to 1.0 represents beginning to end)
                duration = self._currently_playing.active_track.duration

                if duration:
                    target_secs = math.floor(duration * target)
                else:
                    logger.warning("Cannot seek: track duration unknown")
                    return
            else:
                target_secs = int(target)
        elif isinstance(target, str):
            if not utils.is_hmmss(target):
                raise VibinInputError("Time must be in h:mm:ss format")
            target_secs = utils.hmmss_to_secs(target)

        if target_secs is not None:
            requests.post(
                f"http://{self._device_hostname}/smoip/zone/play_control",
                json={"zone": "ZONE1", "position": target_secs},
            )
        else:
            logger.warning(f"Unable to seek to {target}")

    def next_track(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?skip_track=1"
        )

    def previous_track(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?skip_track=-1"
        )

    def repeat(
        self, state: TransportRepeatState | Literal["toggle"] = "toggle"
    ) -> TransportRepeatState:
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?mode_repeat={state}"
        )

        return self._transport_state.repeat

    def shuffle(
        self, state: TransportShuffleState | Literal["toggle"] = "toggle"
    ) -> TransportShuffleState:
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?mode_shuffle={state}"
        )

        return self._transport_state.shuffle

    @property
    def transport_position(self) -> TransportPosition:
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_state/position"
        )

        if response.status_code != 200:
            return 0

        try:
            return int(response.json()["data"]["position"])
        except (KeyError, json.decoder.JSONDecodeError) as e:
            return 0

    @property
    def active_transport_controls(self) -> list[TransportAction]:
        # TODO: Consider just returning self._transport_state.active_controls
        #   rather than retrieving the current active controls.
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/now_playing"
        )

        # TODO: Improve error handling
        if response.status_code != 200:
            return []

        try:
            return self._transform_active_controls(response.json()["data"]["controls"])
        except (KeyError, json.decoder.JSONDecodeError) as e:
            return []

    # -------------------------------------------------------------------------
    # Queue

    @property
    def queue(self) -> Queue:
        return self._queue

    def modify_queue(
        self,
        didl: str,
        action: PlaylistModifyAction = "REPLACE",
        play_from_id: MediaId | None = None,
    ):
        """Add media to the queue using the SMOIP API.

        Args:
            didl: DIDL-Lite XML metadata for the media (album or track).
            action: How to add the media to the queue:
                * "REPLACE": Replace the entire queue. Does not affect playback.
                * "APPEND": Append to the end of the queue. Does not affect
                  playback.
                * "PLAY_NEXT": Insert after the currently playing track. Does
                  not affect playback.
                * "PLAY_NOW": Insert after the currently playing track and
                  immediately start playing the new media.
                * "PLAY_FROM_HERE": Replace the queue with an album and start
                  playing from a specific track (requires play_from_id).
            play_from_id: Only used with PLAY_FROM_HERE action. Specifies the
                track ID within the album to start playing from. Ignored for
                all other actions.
        """
        if not self._media_server:
            raise VibinError("Cannot modify queue: no media server configured")

        # The SMOIP API is sensitive to URL encoding. Using requests' params={}
        # doesn't encode special characters (like = and ?) inside the DIDL
        # the way the streamer expects. Manually construct the URL with proper
        # encoding using quote() with safe="" to encode all special characters.
        server_udn = self._media_server.device_udn
        encoded_didl = quote(didl, safe="")

        url = (
            f"http://{self._device_hostname}/smoip/queue/add"
            f"?action={action}"
            f"&didl={encoded_didl}"
            f"&server_udn={server_udn}"
        )

        if action == "PLAY_FROM_HERE" and play_from_id:
            url += f"&play_from_id={play_from_id}"

        response = requests.get(url)

        if response.status_code != 200:
            logger.warning(
                f"Failed to modify queue: action={action} :: "
                f"Response: {response.status_code} - {response.text}"
            )

    def play_queue_item_position(self, position: int):
        # Find the queue item id associated with the given position, and then
        # play that queue item id.
        #
        # Note: /smoip/zone/play_control does appear to accept a
        # "queue_position" key, but it returns with the message "At least one
        # parameter required".

        try:
            position_queue_id = [
                item for item in self.queue.items if item.position == position
            ][0]

            self.play_queue_item_id(position_queue_id)
        except (AttributeError, IndexError):
            raise VibinNotFoundError(
                f"Could not find Queue item with position: {position}"
            )

    def play_queue_item_id(self, queue_id: int):
        requests.post(
            f"http://{self._device_hostname}/smoip/zone/play_control",
            json={"queue_id": queue_id},
        )

    def queue_clear(self):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/delete",
            json={"start": 0, "delete_all": True},
        )

    def queue_delete_item(self, queue_item_id: int):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/delete",
            json={"ids": [queue_item_id]},
        )

    def queue_move_item(self, queue_item_id: int, from_index: int, to_index: int):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/move",
            json={"id": queue_item_id, "from": from_index, "to": to_index},
        )

    # -------------------------------------------------------------------------
    # Presets

    @property
    def presets(self) -> Presets:
        # TODO: Change to local cache data, as received from websocket.
        response = requests.get(f"http://{self._device_hostname}/smoip/presets/list")

        return Presets(**response.json()["data"])

    def play_preset_id(self, preset_id: int):
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/recall_preset?preset={preset_id}"
        )

    # -------------------------------------------------------------------------
    # UPnP (no-op stubs - StreamMagic uses SMOIP instead of UPnP)

    def subscribe_to_upnp_events(self) -> None:
        pass

    @property
    def upnp_properties(self) -> UPnPProperties:
        return {}

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return {}

    def on_upnp_event(self, service_name: str, event: str):
        pass

    # =========================================================================
    # Additional helpers (not part of Streamer interface).
    # =========================================================================

    # -------------------------------------------------------------------------
    # Static

    @staticmethod
    def _transform_active_controls(controls) -> list[TransportAction]:
        """Transform StreamMagic transport control names to TransportActions."""
        transform_map: dict[str, TransportAction] = {
            "pause": "pause",
            "play": "play",
            "play_pause": "toggle_playback",
            "toggle_shuffle": "shuffle",
            "toggle_repeat": "repeat",
            "track_next": "next",
            "track_previous": "previous",
            "seek": "seek",
            "stop": "stop",
        }

        transformed = []

        for control in controls:
            try:
                transformed.append(transform_map[control])
            except KeyError:
                pass

        return transformed

    def _album_and_track_ids_from_file(self, file: str) -> (str | None, str | None):
        """Determine Album and Track Media IDs from the given filename.

        The source `file` is expected to be a local media file, e.g. a .flac.
        This is making assumptions about the naming conventions used by the
        Asset server, where files are named <track_id>-<album_id>.<ext>.
        """
        if self._media_server is None:
            return None, None

        ids = self._media_server.ids_from_filename(file, ["album", "track"])

        return ids["album"], ids["track"]

    # -------------------------------------------------------------------------
    # State setters

    def _set_active_audio_source(self, source_id: str):
        """Set the active audio source to the one matching the `source_id`."""
        try:
            self._device_state.sources.active = [
                source
                for source in self._device_state.sources.available
                if source.id == source_id
            ][0]

            self._send_system_update()
        except (IndexError, KeyError):
            self._device_state.sources.active = AudioSource()
            logger.warning(
                "Could not determine active audio source from id "
                + f"'{source_id}', setting to empty AudioSource"
            )

    def _set_queue(self):
        """Set the current queue in local state."""
        queue = self._retrieve_queue()

        self._queue = queue
        self._on_update("Queue", self._queue)

        # TODO: Remove
        self._currently_playing.queue = queue
        self._send_currently_playing_update()

    def _set_last_seen_media_ids(self, album_id, track_id):
        self._last_seen_album_id = album_id
        self._last_seen_track_id = track_id

        self._currently_playing.album_media_id = album_id
        self._currently_playing.track_media_id = track_id

    # -------------------------------------------------------------------------
    # Queue helpers

    def _retrieve_queue(self) -> Queue:
        """Retrieve the current queue from the streamer."""
        response = requests.get(
            f"http://{self._device_hostname}/smoip/queue/list"
        )

        payload = response.json()
        queue = Queue.validate(payload["data"])

        # Populate albumMediaId and trackMediaId for each queue item by looking
        # up the album (by title + artist) and track (by album + track number)
        # in the media server.
        if queue.items and self._media_server:
            for item in queue.items:
                if item.metadata and item.metadata.album and item.metadata.artist:
                    # Find the album by title + artist
                    album = self._media_server.album_by_title_and_artist(
                        item.metadata.album,
                        item.metadata.artist
                    )

                    if album:
                        item.albumMediaId = album.id

                        # Find the track by album + track number
                        if item.metadata.track_number:
                            track = self._media_server.track_by_album_and_track_number(
                                album.id,
                                item.metadata.track_number
                            )

                            if track:
                                item.trackMediaId = track.id

        return queue

    # -------------------------------------------------------------------------
    # Helpers to send messages back to Vibin

    def _send_system_update(self):
        self._on_update("System", self._device_state)

    def _send_currently_playing_update(self):
        self._on_update("CurrentlyPlaying", self.currently_playing)

    def _send_transport_state_update(self):
        self._on_update("TransportState", self.transport_state)

    # =========================================================================
    # WebSocket connection to StreamMagic streamer.
    # =========================================================================

    async def _initialize_websocket(self, websocket: WebSocketClientProtocol):
        # Request playhead position updates (these arrive one per sec).
        await websocket.send(
            '{"path": "/zone/play_state/position", "params": {"update": 1}}'
        )

        # Request now-playing updates, so the "controls" information can
        # be used to track active transport controls for TransportState
        # messages.
        await websocket.send('{"path": "/zone/now_playing", "params": {"update": 1}}')

        # Request play state updates (these arrive one per track change).
        await websocket.send('{"path": "/zone/play_state", "params": {"update": 1}}')

        # Request preset updates.
        await websocket.send('{"path": "/presets/list", "params": {"update": 1}}')

        # Request queue updates.
        await websocket.send('{"path": "/queue/info", "params": {"update": 1}}')

        # Request power updates (on/off).
        await websocket.send('{"path": "/system/power", "params": {"update": 100}}')

    def _process_streamer_message(self, update: Data) -> None:
        """Process a single incoming message from the StreamMagic WebSocket server."""
        try:
            self._process_update_message(json.loads(update))
        except (KeyError, json.decoder.JSONDecodeError):
            # TODO: This currently quietly ignores unexpected payload formats
            #   or missing keys. Consider adding error handling if errors need
            #   to be announced.
            pass

    def _process_update_message(self, update_dict: dict[str, Any]):
        if update_dict["path"] == "/zone/play_state":
            # Current play state ----------------------------------------------
            play_state = update_dict["params"]["data"]

            # Extract current transport state.
            try:
                self._transport_state.play_state = play_state["state"]
                self._transport_state.repeat = play_state["mode_repeat"]
                self._transport_state.shuffle = play_state["mode_shuffle"]
            except KeyError:
                pass

            # Extract the active track details from play_state metadata.
            # When determining the active track details, if we don't have a
            # title but we *do* have a station then we use the station as the
            # title. This handles internet radio cases.
            #
            # TODO: Improve handling of the current track. For local media we
            #   mostly ignore this information and use the media id to get the
            #   full track details from the media server. But for non-local
            #   playback it would be nice to have a more flexible notion of
            #   a current track (which accounts for a variety of sources).

            current_track_info = play_state["metadata"]
            if "title" not in current_track_info and "station" in current_track_info:
                current_track_info["title"] = current_track_info["station"]

            try:
                self._currently_playing.active_track = ActiveTrack(
                    **current_track_info
                )
            except KeyError:
                pass

            # Extract the format details from play_state metadata.
            try:
                self._currently_playing.format = MediaFormat(**play_state["metadata"])
            except KeyError:
                pass

            # Note: When a StreamMagic device comes out of standby mode, its
            # play_state update message does not include some fields.
            #
            # If this play_state update comes in while the player is paused and
            # the title matches the queue item title for the play_position, then
            # we fill in some of the missing fields by taking their values from
            # the matching queue item. This isn't ideal.
            #
            # TODO: Is there some way to ensure the play_state message always
            #  includes all of the same fields so we don't have to look
            #  elsewhere for any missing values?

            if self._transport_state.play_state == "pause":
                try:
                    active_track = self._currently_playing.active_track
                    queue_play_position = self._queue.play_position

                    if queue_play_position is not None and self._queue.items:
                        current_queue_item = self._queue.items[queue_play_position]

                        # If any of the active_track details are None, then fill
                        # them with info from the current queue item
                        # (assuming the queue item title matches the active
                        # track title).
                        if (
                            current_queue_item.metadata
                            and current_queue_item.metadata.title == active_track.title
                        ):
                            if active_track.album is None:
                                active_track.album = current_queue_item.metadata.album
                            if active_track.artist is None:
                                active_track.artist = current_queue_item.metadata.artist
                            if active_track.duration is None:
                                active_track.duration = current_queue_item.metadata.duration
                except (IndexError, KeyError, TypeError) as e:
                    pass

            self._send_currently_playing_update()
            self._send_transport_state_update()
        elif update_dict["path"] == "/zone/play_state/position":
            # Transport playhead position -------------------------------------

            self._on_update("Position", update_dict["params"]["data"])
        elif update_dict["path"] == "/zone/now_playing":
            # Active transport controls, audio source, and device display -----

            # TODO: This message is received every second (because of playhead
            #   position information). Consider consequences for updating data,
            #   when those updates are sent to on_update, etc.

            self._transport_state.active_controls = [
                control
                for control in self._transform_active_controls(
                    update_dict["params"]["data"]["controls"]
                )
            ]

            self._send_transport_state_update()

            # TODO: Figure out what to do with current audio source. This call
            #   to _set_active_audio_source will ensure the source is set for
            #   the next StateVars message publish.
            audio_source_id = update_dict["params"]["data"]["source"]["id"]

            self._set_active_audio_source(audio_source_id)

            # Media IDs should only be sent to any clients when the current
            # source is a MEDIA_PLAYER.
            if audio_source_id != "MEDIA_PLAYER":
                self._set_last_seen_media_ids(None, None)

            try:
                display_info = update_dict["params"]["data"]["display"]
                self._device_state.display = StreamerDeviceDisplay(**display_info)

                if DeepDiff(display_info, self._device_display_raw) != {}:
                    self._device_display_raw = display_info
                    self._send_system_update()
            except KeyError:
                pass
        elif update_dict["path"] == "/queue/info":
            # Queue -----------------------------------------------------------
            #
            # We treat this as just an announcement that the queue has changed
            # in some way. The payload we receive here is just a small amount
            # of information, so we ignore it and instead request the full
            # queue state from the streamer.

            self._set_queue()
        elif update_dict["path"] == "/presets/list":
            # Presets ---------------------------------------------------------

            self._on_update("Presets", update_dict["params"]["data"])
        elif update_dict["path"] == "/system/power":
            # System power ----------------------------------------------------

            power = update_dict["params"]["data"]["power"]
            self._device_state.power = "on" if power == "ON" else "off"
            self._send_system_update()
        else:
            logger.warning(f"Unknown message: {json.dumps(update_dict)}")
