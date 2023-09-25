import array
import asyncio
import atexit
import base64
import functools
import json
import math
import pathlib
import queue
import re
import sys
from typing import Literal
from urllib.parse import urlparse
import uuid
import xml.etree.ElementTree as ET

from deepdiff import DeepDiff
from lxml import etree
import requests
import untangle
import upnpclient
from upnpclient.marshal import marshal_value
import websockets
import xmltodict

from vibin import utils, VibinDeviceError, VibinError, VibinInputError
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.models import (
    ActivePlaylist,
    ActivePlaylistEntry,
    ActiveTrack,
    AudioSource,
    AudioSources,
    CurrentlyPlaying,
    MediaFormat,
    MediaStream,
    PlaylistModifiedHandler,
    PlaylistModifyAction,
    PowerState,
    Presets,
    StreamerDeviceDisplay,
    StreamerState,
    TransportAction,
    TransportRepeatState,
    TransportShuffleState,
    TransportState,
    UPnPServiceSubscriptions,
)
from vibin.types import (
    SeekTarget,
    TransportPosition,
    UPnPServiceName,
    UpdateMessageHandler,
    UPnPPropertyName,
    UPnPProperties,
    UPnPPropertyChangeHandlers,
)
from vibin.streamers import Streamer
from vibin.utils import UPnPSubscriptionManagerThread


# See Streamer interface for method documentation.

# -----------------------------------------------------------------------------
# NOTE: Media IDs only make sense when streaming from a local source.
# -----------------------------------------------------------------------------

# TODO: This class would benefit from some overall refactoring. Its current
#  implementation isn't very clear and is showing its hacked-together history.
# TODO: Investigate migrating from UPnP to SMOIP where possible.
# TODO: Consider using httpx instead of requests, and making some of these
#   methods (particularly the ones that use SMOIP) async.
# TODO: Consider using upnpclient.SOAPError more for error handling.
# TODO: Consider using upnpclient.UPNPError more for error handling.
# TODO: Can end up with multiple UPnP subscriptions to each service.


class StreamMagicBadNavigatorError(Exception):
    pass


def retry_on_bad_navigator(method):
    """Decorator to retry the method call on StreamMagicBadNavigatorError."""
    @functools.wraps(method)
    def wrapper_retry_on_bad_navigator(self, *method_args, **method_kwargs):
        try:
            # Invoke the decorated method.
            return method(self, *method_args, **method_kwargs)
        except StreamMagicBadNavigatorError:
            # If the decorated method raised StreamMagicBadNavigatorError then
            # attempt to acquire a fresh navigator from the streamer. Only try
            # this once.
            logger.info("Attempting to re-acquire StreamMagic navigator")
            self._initialize_navigator()

            try:
                return method(self, *method_args, **method_kwargs)
            except StreamMagicBadNavigatorError:
                raise VibinDeviceError(f"Could not re-acquire StreamMagic navigator")

    return wrapper_retry_on_bad_navigator


class StreamMagic(Streamer):
    model_name = "StreamMagic"

    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
        on_playlist_modified: PlaylistModifiedHandler | None = None,
    ):
        """Implement the Streamer interface for StreamMagic streamers."""
        self._device = device
        self._upnp_subscription_callback_base = upnp_subscription_callback_base
        self._on_update = on_update
        self._on_playlist_modified = on_playlist_modified

        self._device_hostname = urlparse(device.location).hostname

        self._device_state: StreamerState = StreamerState(
            name=self._device.friendly_name,
            power=None,
            sources=AudioSources(),
            display=StreamerDeviceDisplay(),
        )

        self._upnp_properties: UPnPProperties = {}
        self._currently_playing: CurrentlyPlaying = CurrentlyPlaying()
        self._transport_state: TransportState = TransportState()
        self._device_display_raw = {}
        self._cached_playlist_entries: list[ActivePlaylistEntry] = []

        self._disconnected = False
        self._media_server: MediaServer | None = None
        self._instance_id = 0  # StreamMagic implements a static AVTransport instance
        self._websocket_thread = None
        self._websocket_timeout = 1

        self._uu_vol_control = device.UuVolControl
        self._av_transport = device.AVTransport
        self._playlist_extension = device.PlaylistExtension

        # Set up UPnP event handlers.
        self._upnp_property_change_handlers: UPnPPropertyChangeHandlers = {
            (
                UPnPServiceName("AVTransport"),
                UPnPPropertyName("LastChange"),
            ): self._upnp_last_change_event_handler,
            (
                UPnPServiceName("PlaylistExtension"),
                UPnPPropertyName("IdArray"),
            ): self._upnp_playlist_id_array_event_handler,
            (
                UPnPServiceName("UuVolControl"),
                UPnPPropertyName("CurrentPlaylistTrackID"),
            ): self._upnp_current_playlist_track_id_event_handler,
            (
                UPnPServiceName("UuVolControl"),
                UPnPPropertyName("PlaybackXML"),
            ): self._upnp_current_playback_event_handler,
        }

        # Configure thread for managing UPnP subscriptions
        self._upnp_subscription_manager_queue = queue.Queue()

        if self._upnp_subscription_callback_base is None:
            self._upnp_subscription_manager_thread = None
            logger.warning(
                "No UPnP subscription base provided; cannot subscribe to UPnP events"
            )
        else:
            self._upnp_subscription_manager_thread = UPnPSubscriptionManagerThread(
                device=self._device,
                cmd_queue=self._upnp_subscription_manager_queue,
                subscription_callback_base=self._upnp_subscription_callback_base,
                services=[
                    self._av_transport,
                    self._playlist_extension,
                    self._uu_vol_control,
                ],
            )
            self._upnp_subscription_manager_thread.start()

        ET.register_namespace("", "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/")
        ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
        ET.register_namespace("upnp", "urn:schemas-upnp-org:metadata-1-0/upnp/")
        ET.register_namespace("dlna", "urn:schemas-dlna-org:metadata-1-0/")

        # This unique navigator name ensures that multiple vibins can run
        # concurrently. This unique-navigator approach is done because vibin
        # releases the navigator when it shuts down, which can cause problems
        # for other still-running vibins which might have been using that
        # navigator. This may or me not be how navigators are intended to be
        # used. This approach also assumes the streamer is automatically
        # cleaning up old navigators (if vibin's navigator release fails for
        # some reason).
        self._navigator_name = f"vibin-{str(uuid.uuid4())[:8]}"
        self._navigator_id = None
        self._initialize_navigator()

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

        # Current playlist.
        self._playlist_id_array = None
        self._set_current_playlist_entries()

        # Current playlist track index.
        try:
            response = device.UuVolControl.GetCurrentPlaylistTrack()
            self._set_current_playlist_track_index(response["CurrentPlaylistTrackID"])
        except Exception:
            # TODO
            pass

        if self._on_update:
            self._websocket_thread = utils.StoppableThread(
                target=self._handle_websocket_to_streamer
            )
            self._websocket_thread.start()

        # Keep track of last seen ("currently playing") track and album IDs.
        # This is done to facilitate injecting this information into payloads
        # which want it, but it isn't already there when sent from the streamer.
        # Future updates to the track and album IDs come in via UPnP updates
        # (see self._determine_current_media_ids()).
        self._set_last_seen_media_ids(None, None)

        try:
            # See if any currently-playing media IDs can be found at startup
            response = device.UuVolControl.GetPlaybackDetails(
                NavigatorId=self._navigator_id
            )

            # Determine the currently-streamed URL, and use it to extract IDs.
            stream_url = untangle.parse(
                response["RetPlaybackXML"]
            ).reciva.playback_details.stream.url.cdata

            this_album_id, this_track_id = self._album_and_track_ids_from_file(stream_url)

            logger.info(f"Found currently-playing local media IDs")
            self._set_last_seen_media_ids(this_album_id, this_track_id)
        except Exception:
            # TODO: Investigate which exceptions to handle
            logger.info(f"No currently-playing local media IDs found")

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

    def on_shutdown(self) -> None:
        if self._disconnected:
            return

        logger.info("StreamMagic disconnect requested")
        self._disconnected = True

        self._release_navigator()
        self._upnp_subscription_manager_queue.put_nowait("SHUTDOWN")

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
    #
    # Interacting with the transport is currently a mix of UPnP requests and
    # SMOIP HTTP requests.

    @property
    def transport_state(self) -> TransportState:
        return self._transport_state

    def play(self):
        if self._transport_state.play_state == "play":
            return

        if "toggle_playback" in self._transport_state.active_controls:
            self.toggle_playback()
        else:
            self._av_transport.Play(InstanceID=self._instance_id, Speed="1")

    def pause(self):
        if self._transport_state.play_state == "pause":
            return

        if "toggle_playback" in self._transport_state.active_controls:
            self.toggle_playback()
        else:
            self._av_transport.Pause(InstanceID=self._instance_id)

    def toggle_playback(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?action=toggle"
        )

    def stop(self):
        self._av_transport.Stop(InstanceID=self._instance_id)

    def seek(self, target: SeekTarget):
        if "seek" not in self.active_transport_controls:
            # TODO: Establish consistent way of handling "currently unavailable"
            #   features.
            raise VibinError("Seek currently unavailable")

        target_hmmss = None

        # TODO: Fix handling of float vs. int. All numbers come in as floats,
        #   so what to do with 1/1.0 is ambiguous (here we treat is as 1 second
        #   not the end of the 0-1 normalized range).
        if isinstance(target, float):
            if target == 0:
                target_hmmss = utils.secs_to_hmmss(0)
            elif target < 1:
                media_info = self._av_transport.GetMediaInfo(InstanceID=0)
                duration_secs = utils.hmmss_to_secs(media_info["MediaDuration"])

                target_hmmss = utils.secs_to_hmmss(math.floor(duration_secs * target))
            else:
                target_hmmss = utils.secs_to_hmmss(int(target))
        elif isinstance(target, str):
            if not utils.is_hmmss(target):
                raise VibinInputError("Time must be in h:mm:ss format")

            target_hmmss = target

        if target_hmmss:
            self._av_transport.Seek(
                InstanceID=self._instance_id,
                Unit="ABS_TIME",
                Target=target_hmmss,
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

        repeat_state = self._playlist_extension.Repeat()
        self._transport_state.repeat = "all" if repeat_state["aRepeat"] is True else "off"

        return self._transport_state.repeat

    def shuffle(
        self, state: TransportShuffleState | Literal["toggle"] = "toggle"
    ) -> TransportShuffleState:
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?mode_shuffle={state}"
        )

        shuffle_state = self._playlist_extension.Shuffle()
        self._transport_state.shuffle = "all" if shuffle_state["aShuffle"] is True else "off"

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
    # Active Playlist

    @property
    def playlist(self) -> ActivePlaylist:
        return self._currently_playing.playlist

    @retry_on_bad_navigator
    def modify_playlist(
        self,
        metadata: str,
        action: PlaylistModifyAction = "REPLACE",
        insert_index: int | None = None,  # Only used by INSERT action
    ):
        try:
            if action == "INSERT":
                # INSERT. This works for Tracks only (not Albums).
                # TODO: Add check to ensure metadata is for a Track.
                result = self._uu_vol_control.InsertPlaylistTrack(
                    InsertPosition=insert_index, TrackData=metadata
                )
            else:
                # REPLACE, PLAY_NOW, PLAY_NEXT, PLAY_FROM_HERE, APPEND
                queue_folder_response = self._uu_vol_control.QueueFolder(
                    ServerUDN=self._media_server.device_udn,
                    Action=action,
                    NavigatorId=self._navigator_id,
                    ExtraInfo="",
                    DIDL=metadata,
                )

                if queue_folder_response["Result"] == "BAD_NAVIGATOR":
                    logger.warning("StreamMagic navigator is bad")
                    raise StreamMagicBadNavigatorError()
        except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
            # TODO: Look at using VibinDeviceError wherever things like
            #   _uu_vol_control are being used.
            raise VibinDeviceError(e)

    def play_playlist_index(self, index: int):
        self._uu_vol_control.SetCurrentPlaylistTrack(CurrentPlaylistTrackID=index)

    def play_playlist_id(self, playlist_id: int):
        try:
            playlist_index = self._retrieve_active_playlist_array().index(playlist_id)
            self.play_playlist_index(playlist_index)
        except ValueError:
            pass

    def playlist_clear(self):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/delete",
            json={"start": 0, "delete_all": True},
        )

    def playlist_delete_entry(self, playlist_id: int):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/delete",
            json={"ids": [playlist_id]},
        )

    def playlist_move_entry(self, playlist_id: int, from_index: int, to_index: int):
        requests.post(
            f"http://{self._device_hostname}/smoip/queue/move",
            json={"id": playlist_id, "from": from_index, "to": to_index},
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
    # UPnP

    def subscribe_to_upnp_events(self) -> None:
        self._upnp_subscription_manager_queue.put_nowait("SUBSCRIBE")

    @property
    def upnp_properties(self) -> UPnPProperties:
        return self._upnp_properties

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return self._upnp_subscription_manager_thread.subscriptions

    def on_upnp_event(self, service_name: UPnPServiceName, event: str):
        logger.debug(f"{self.name} received {service_name} event:\n\n{event}\n")

        property_set = etree.fromstring(event)

        # TODO: Migrate to untangle
        # parsed = untangle.parse(event)
        # parsed.e_propertyset.children[0].LastChange.cdata

        for property in property_set:
            property_element = property[0]

            self._set_upnp_property(
                service_name=service_name,
                property_name=property_element.tag,
                property_value_xml=property_element.text,
            )

        self._set_vibin_upnp_properties()

    # =========================================================================
    # Additional helpers (not part of Streamer interface).
    # =========================================================================

    def _initialize_navigator(self):
        """Initialize the StreamMagic navigator.

        The navigator appears to be a unique identifier required to invoke some
        UPnP calls on the StreamMagic streamer; specifically UuVolControl calls.
        This method requests a navigator from the streamer, supplying vibin's
        navigator name and receiving a unique navigator ID in return. This
        navigator ID is then used for some streamer calls.
        """
        nav_check = self.device.UuVolControl.IsRegisteredNavigatorName(
            NavigatorName=self._navigator_name
        )

        if nav_check["IsRegistered"]:
            # Navigator is already registered.
            self._navigator_id = nav_check["RetNavigatorId"]
        else:
            # Need to request a navigator.
            self._navigator_id = None

            try:
                new_nav = self.device.UuVolControl.RegisterNamedNavigator(
                    NewNavigatorName=self._navigator_name
                )

                try:
                    self._navigator_id = new_nav["RetNavigatorId"]
                except KeyError:
                    raise VibinDeviceError(
                        f"Could not acquire StreamMagic navigator: {new_nav}"
                    )

                logger.info(
                    f"StreamMagic navigator name: {self._navigator_name} "
                    + f"id: {self._navigator_id}"
                )
            except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
                logger.error(
                    "Could not acquire StreamMagic navigator. If device is in "
                    + "standby, power it on and try again."
                )
                raise VibinDeviceError(f"Could not acquire StreamMagic navigator: {e}")

    def _release_navigator(self):
        """Release the StreamMagic navigator."""
        if self._navigator_id is not None:
            try:
                logger.info(
                    f"Releasing StreamMagic navigator; name: {self._navigator_name} " +
                    f"id: {self._navigator_id}"
                )
                self._uu_vol_control.ReleaseNavigator(NavigatorId=self._navigator_id)
            except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
                logger.error(f"Could not release StreamMagic navigator: {e}")

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

    @staticmethod
    def _album_and_track_ids_from_file(file: str) -> (str | None, str | None):
        """Determine Album and Track Media IDs from the given filename.

        The source `file` is expected to be a local media file, e.g. a .flac.
        This is making assumptions about the naming conventions used by the
        Asset server, where files are named <track_id>-<album_id>.<ext>.
        """
        filename_only = pathlib.Path(file).stem

        # It seems that the track id can itself include a hyphen whereas the
        # album id won't.
        match = re.match(r"^(.*-([^-]+))$", filename_only)

        if match and len(match.groups(0)) == 2:
            this_track_id = match.groups(0)[0]
            this_album_id = match.groups(0)[1]

            if this_album_id == "0":
                # An album id of "0" seems to mean that the album id is unknown,
                # so strip it off.
                this_album_id = None
                this_track_id = this_track_id.removesuffix("-0")
            else:
                # We have a known album id, so strip it off of the track id.
                this_track_id = this_track_id.replace(f"-{this_album_id}", "")

            return this_album_id, this_track_id

        return None, None

    # -------------------------------------------------------------------------
    # State setters

    def _determine_current_media_ids(self, details):
        """Set current media ids from an incoming `details` UPnP event.

        This exists only to set the last seen track and album IDs, which are
        extracted from the current streaming filename. This assumes that the
        filename being streamed contains these IDs.

        TODO: Can the last seen track and album IDs be set from information
            coming in from the WebSocket instead.
        """
        # If the current playback details includes the streamed filename, then
        # extract the Track ID and Album ID.
        try:
            stream_url = details["stream"]["url"]
            this_album_id, this_track_id = self._album_and_track_ids_from_file(
                stream_url
            )

            send_update = True

            if this_album_id and this_track_id:
                if this_track_id != self._last_seen_track_id:
                    self._set_last_seen_media_ids(this_album_id, this_track_id)
                else:
                    send_update = False
            else:
                # A streamed file was found, but the track and album ids could
                # not be determined. Play it safe and set the IDs to None to
                # ensure clients aren't provided incorrect/misleading ids.
                self._set_last_seen_media_ids(None, None)

            # Force send of a CurrentlyPlaying message to ensure any listening
            # clients get the new track/album id information without having
            # to wait for a normal CurrentlyPlaying update.
            if send_update:
                self._send_currently_playing_update()
        except KeyError:
            # No streamed filename found in the playback details
            pass

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

    def _set_current_playlist_entries(self):
        """Set the active playlist entries in local state."""
        playlist_entries = self._retrieve_active_playlist_entries()

        self._currently_playing.playlist.entries = playlist_entries
        self._send_currently_playing_update()

    def _set_current_playlist_track_index(self, index: int):
        self._currently_playing.playlist.current_track_index = index
        self._send_currently_playing_update()

    def _set_last_seen_media_ids(self, album_id, track_id):
        self._last_seen_album_id = album_id
        self._last_seen_track_id = track_id

        self._currently_playing.album_media_id = album_id
        self._currently_playing.track_media_id = track_id

    # -------------------------------------------------------------------------
    # Playlist helpers

    def _retrieve_active_playlist_array(self) -> list[int]:
        """Retrieve the active playlist ID array from the streamer."""
        try:
            response = self._device.PlaylistExtension.IdArray()

            # The array comes back as base64-encoded array of ints.
            playlist_encoded = response["aIdArray"]
            playlist_decoded = base64.b64decode(playlist_encoded)
            playlist_array = array.array("I", playlist_decoded)

            if sys.byteorder == "little":
                playlist_array.byteswap()

            return list(playlist_array)
        except Exception:
            # TODO: What exception gets thrown here?
            logger.warning("Could not determine the streamer's active playlist IDs")
            return []

    def _retrieve_active_playlist_entries(self) -> list[ActivePlaylistEntry]:
        """Retrieve the active playlist entries from the streamer."""
        playlist_entry_ids = self._retrieve_active_playlist_array()

        # Retrieve the playlist via UPnP.
        # TODO: Can this information be plucked from a streamer WebSocket
        #   message? If so, would that path be simpler?
        response = self._device.PlaylistExtension.ReadList(
            aIdList=",".join([str(id) for id in playlist_entry_ids])
        )

        key_tag_map = {
            "album": "upnp:album",
            "artist": "upnp:artist",
            "genre": "upnp:genre",
            "albumArtURI": "upnp:albumArtURI",
            "originalTrackNumber": "upnp:originalTrackNumber",
            "title": "dc:title",
        }

        playlist_entries = etree.fromstring(response["aMetaDataList"])

        # Construct a sanitized playlist entry for each of the raw entries
        # retrieved via UPnP. Store all entries in a map keyed by entry ID.
        entry_id_to_playlist_entry = {}

        for index, playlist_entry in enumerate(playlist_entries):
            id = int(playlist_entry.findtext("Id").replace("l", ""))
            uri = playlist_entry.findtext("Uri")

            metadata_str = playlist_entry.findtext("MetaData")
            metadata_elem = etree.fromstring(metadata_str)
            ns = metadata_elem.nsmap

            item_elem = metadata_elem.find("item", namespaces=ns)

            entry_data = {
                "id": id,
                "index": index,
                "uri": uri,
                "trackMediaId": None,
                "albumMediaId": None,
            }

            for key, tag in key_tag_map.items():
                entry_data[key] = item_elem.findtext(tag, namespaces=ns)

            entry_data["duration"] = item_elem.find("res", namespaces=ns).attrib[
                "duration"
            ]

            this_album_id, this_track_id = self._album_and_track_ids_from_file(uri)

            entry_data["albumMediaId"] = this_album_id
            entry_data["trackMediaId"] = this_track_id

            entry_id_to_playlist_entry[id] = entry_data

        results = []

        # Create an array of playlist entries, in the same order as the ID list
        # retrieved earlier. This ID list is the source of truth for entry order.
        for playlist_entry_id in playlist_entry_ids:
            try:
                results.append(entry_id_to_playlist_entry[playlist_entry_id])
            except KeyError:
                pass

        # Check whether the playlist has changed from the last time the playlist
        # was cached in local state. If the playlist has changed then we'll want
        # to announce that.
        cached_playlist_media_ids = [
            entry.trackMediaId for entry in self._cached_playlist_entries
        ]
        active_playlist_media_ids = [entry["trackMediaId"] for entry in results]

        # Coerce the playlist into a list of PlaylistEntry objects
        results_as_entries = [ActivePlaylistEntry(**result) for result in results]

        if cached_playlist_media_ids != active_playlist_media_ids:
            # NOTE: All changes to the active playlist should be detected here,
            #   regardless of where they originated (a Vibin client, another
            #   app like the StreamMagic iOS app, etc).
            self._on_playlist_modified(results_as_entries)

        self._cached_playlist_entries = results_as_entries

        return results_as_entries

    # -------------------------------------------------------------------------
    # Helpers to send messages back to Vibin

    def _send_system_update(self):
        self._on_update("System", self._device_state)

    def _send_currently_playing_update(self):
        self._on_update("CurrentlyPlaying", self.currently_playing)

    def _send_transport_state_update(self):
        self._on_update("TransportState", self.transport_state)

    # -------------------------------------------------------------------------
    # UPnP event handling
    # -------------------------------------------------------------------------

    def _upnp_last_change_event_handler(
        self,
        service_name: UPnPServiceName,
        property_value: str,
    ):
        """Handle "LastChange" UPnP events. from the AVTransport service."""
        nested_element = etree.fromstring(property_value)
        instance_element = nested_element.find(
            "InstanceID", namespaces=nested_element.nsmap
        )

        result = {}

        for parameter in instance_element:
            param_name = etree.QName(parameter)

            try:
                _, marshaled_value = marshal_value(
                    self._device[service_name].statevars[param_name.localname][
                        "datatype"
                    ],
                    parameter.get("val"),
                )

                result[param_name.localname] = marshaled_value
            except KeyError:
                # TODO: Log
                pass

        return result

    def _upnp_playlist_id_array_event_handler(
        self, service_name: UPnPServiceName, property_value: str
    ):
        """Handle "IdArray" UPnP events from the PlaylistExtension service.

        The IdArray event is received when the playlist changes, so this is an
        entrypoint into knowing when the playlist has changed. Playlist changes
        might come from us (e.g. Vibin adding a playlist entry), or from
        somewhere else (maybe the StreamMagic app running on iOS). This is
        considered the playlist-change source of truth.
        """
        if property_value != self._playlist_id_array:
            self._playlist_id_array = property_value
            self._set_current_playlist_entries()

    def _upnp_current_playlist_track_id_event_handler(
        self, service_name: UPnPServiceName, property_value: str
    ):
        """Handle "CurrentPlaylistTrackID" UPnP events from the UuVolControl service.

        The CurrentPlaylistTrackID event is the source of truth for when the
        streamer has started playing a new playlist entry.
        """
        self._set_current_playlist_track_index(int(property_value))

    def _upnp_current_playback_event_handler(
        self, service_name: UPnPServiceName, property_value: str
    ):
        """Handle "PlaybackXML" UPnP events from the UuVolControl service.

        The PlaybackXML event contains media stream information.
        """
        # Extract current Stream details from playback information.
        parsed = untangle.parse(property_value)

        try:
            self._currently_playing.stream = MediaStream(
                url=parsed.children[0].playback_details.stream.url.cdata
            )
        except (IndexError, AttributeError):
            pass

    def _set_upnp_property(
        self,
        service_name: UPnPServiceName,
        property_name: UPnPPropertyName,
        property_value_xml: str,
    ):
        if service_name not in self._upnp_properties:
            self._upnp_properties[service_name] = {}

        if upnp_property_handler := self._upnp_property_change_handlers.get(
            (service_name, property_name)
        ):
            self._upnp_properties[service_name][property_name] = upnp_property_handler(
                service_name, property_value_xml
            )
        else:
            _, marshaled_value = marshal_value(
                self._device[service_name].statevars[property_name]["datatype"],
                property_value_xml,
            )

            self._upnp_properties[service_name][property_name] = marshaled_value

        # For each state var which contains XML text (i.e. any field name ending
        # in "XML"), we attempt to create a JSON equivalent.
        if property_name.endswith("XML"):
            json_var_name = f"{property_name[0:-3]}JSON"
            xml = property_value_xml

            if xml:
                # TODO: This is not scalable (but html.escape also escapes tags)
                xml = xml.replace("&", "&amp;")

                try:
                    self._upnp_properties[service_name][
                        json_var_name
                    ] = xmltodict.parse(xml)
                except xml.parsers.expat.ExpatError as e:
                    logger.error(
                        f"Could not convert XML to JSON for "
                        + f"{service_name}:{property_name}: {e}"
                    )

    def _set_vibin_upnp_properties(self):
        # TODO: Can this be removed once last seen track and album IDs are
        #   extracted from a StreamMagic WebSocket update.
        try:
            self._determine_current_media_ids(
                self._upnp_properties["UuVolControl"]["PlaybackJSON"]["reciva"][
                    "playback-details"
                ]
            )
        except KeyError:
            pass

    # =========================================================================
    # WebSocket connection to StreamMagic streamer.
    # =========================================================================

    def _handle_websocket_to_streamer(self):
        """Handle the WebSocket connection to the StreamMagic WebSocket server.

        On connection, subscribe to a variety of StreamMagic events. Then
        continue to process messages as they come in, until told to stop
        (probably because the system is shutting down).
        """
        async def async_websocket_manager():
            uri = f"ws://{self._device_hostname}:80/smoip"
            logger.info(f"Connecting to {self.name} WebSocket server on {uri}")

            wait_for_message = True

            # TODO: Handle condition where the uri is technically valid, but the
            #   connection cannot be established.

            try:
                # The "for" iteration automatically takes care of reconnects.
                async for websocket in websockets.connect(
                    uri,
                    ssl=None,
                    extra_headers={
                        "Origin": "vibin",
                    },
                ):
                    try:
                        logger.info(f"Successfully connected to {self.name} WebSocket server")

                        # Request playhead position updates (these arrive one per sec).
                        await websocket.send(
                            '{"path": "/zone/play_state/position", "params": {"update": 1}}'
                        )

                        # Request now-playing updates, so the "controls" information can
                        # be used to track active transport controls for TransportState
                        # messages.
                        await websocket.send(
                            '{"path": "/zone/now_playing", "params": {"update": 1}}'
                        )

                        # Request play state updates (these arrive one per track change).
                        await websocket.send(
                            '{"path": "/zone/play_state", "params": {"update": 1}}'
                        )

                        # Request preset updates.
                        await websocket.send(
                            '{"path": "/presets/list", "params": {"update": 1}}'
                        )

                        # Request power updates (on/off).
                        await websocket.send(
                            '{"path": "/system/power", "params": {"update": 100}}'
                        )

                        while wait_for_message:
                            try:
                                # Wait for an incoming message
                                update = await asyncio.wait_for(
                                    websocket.recv(), timeout=self._websocket_timeout
                                )
                            except asyncio.TimeoutError:
                                if not self._websocket_thread.stop_event.is_set():
                                    # Keep listening for incoming messages
                                    continue

                                # The thread we're running in has been told to stop
                                wait_for_message = False

                            try:
                                self._process_streamer_message(json.loads(update))
                            except (KeyError, json.decoder.JSONDecodeError) as e:
                                # TODO: This currently quietly ignores unexpected
                                #   payload formats or missing keys. Consider adding
                                #   error handling if errors need to be announced.
                                pass

                        logger.info(f"WebSocket connection to {self.name} closed by Vibin")
                        return
                    except websockets.ConnectionClosed:
                        # Attempt a re-connect when the streamer drops the connection
                        logger.warning(
                            f"Lost connection to {self.name} WebSocket server; " +
                            "attempting reconnect"
                        )

                        # Continue the "for" loop, which will trigger a reconnect.
                        continue
            except websockets.WebSocketException as e:
                logger.error(f"WebSocket error from {self.name}: {e}")

        asyncio.run(async_websocket_manager())

    def _process_streamer_message(self, update_dict: dict) -> None:
        """Process a single incoming message from the StreamMagic WebSocket server."""
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
            # the title matches playlist title for the queue_index, then we fill
            # in some of the missing fields by taking their values from the
            # matching playlist entry. This isn't ideal.
            #
            # TODO: Is there some way to ensure the play_state message always
            #  includes all of the same fields so we don't have to look
            #  elsewhere for any missing values?

            if self._transport_state.play_state == "pause":
                try:
                    active_track = self._currently_playing.active_track
                    current_playlist_index = self._currently_playing.playlist.current_track_index

                    if current_playlist_index is not None:
                        current_playlist_entry = self._currently_playing.playlist.entries[
                            current_playlist_index
                        ]

                        # If any of the active_track details are None, then fill
                        # them with info from the current playlist entry
                        # (assuming the playlist entry title matches the active
                        # track title).
                        if current_playlist_entry.title == active_track.title:
                            if active_track.album is None:
                                active_track.album = current_playlist_entry.album
                            if active_track.artist is None:
                                active_track.artist = current_playlist_entry.artist
                            if active_track.duration is None:
                                active_track.duration = utils.hmmss_to_secs(
                                    current_playlist_entry.duration
                                )
                except (IndexError, KeyError) as e:
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
