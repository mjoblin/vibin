import array
import asyncio
import atexit
import base64
import json
import math
import pathlib
import re
import sys
import threading
import time
from typing import Any, Callable, List, NewType, Optional
from urllib.parse import urlparse
import xml.etree.ElementTree as ET  # TODO: Replace with lxml

from deepdiff import DeepDiff
from lxml import etree
import requests
from requests.exceptions import HTTPError
import upnpclient
from upnpclient.marshal import marshal_value
import websockets
import xmltodict

from ..logger import logger
from vibin import VibinDeviceError
from vibin.types_foo import ServiceSubscriptions, Subscription
from vibin.mediasources import MediaSource
from vibin.streamers import SeekTarget, Streamer, TransportState
from .. import utils

# TODO: Consider using httpx instead of requests, and making some of these
#   methods (particularly the ones that use SMOIP) async.

# -----------------------------------------------------------------------------
# NOTE: Media IDs only make sense when streaming from a local source. This
#   should impact when non-None Media IDs are sent to any clients.
# -----------------------------------------------------------------------------


# TODO: upnpclient.SOAPError
# TODO: upnpclient.UPNPError
# TODO: Consider migrating core (if any) capabilities to Streamer class
# TODO: Can end up with multiple subscriptions to each service

# open websocket:
#   ws://10.0.0.13:80/smoip
# send: {"path": "/zone/play_state/position", "params": {"update": 1}}
# will then receive an update on position per second
# "position" appears to be the number of seconds into the track
# get a message for 0, then 1, 2, 3, etc...
#
# {
#   "path": "/zone/play_state/position",
#   "type": "update",
#   "result": 200,
#   "message": "OK",
#   "params": {
#     "zone": "ZONE1",
#     "data": {
#       "position": 21
#     }
#   }
# }

class StoppableThread(threading.Thread):
    def __init__(self,  *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def stopped(self):
        return self.stop_event.is_set()


ServiceName = NewType("ServiceName", str)  # TODO: Is NewType right?

StateVarName = NewType("StateVarName", str)
StateVars = dict[ServiceName, dict[StateVarName, Any]]
StateVarHandlers = dict[StateVarName, Callable[[ServiceName, etree.Element], Any]]


class CXNv2(Streamer):
    model_name = "CXNv2"

    def __init__(
            self,
            device: upnpclient.Device,
            subscribe_callback_base: Optional[str],
            updates_handler=None,
            on_playlist_modified=None,
    ):
        self._device = device
        self._subscribe_callback_base = subscribe_callback_base
        self._updates_handler = updates_handler
        self._on_playlist_modified = on_playlist_modified
        self._ignore_playlist_updates = None

        self._device_hostname = urlparse(device.location).hostname

        self._system_state = {
            "name": self._device.friendly_name,
            "power": None,
        }

        self._state_vars: StateVars = {}

        self._vibin_vars = {
            "audio_sources": [],
            "current_audio_source": None,
            "current_playlist": None,
            "current_playlist_track_index": None,
            "current_playback_details": None,
        }

        self._play_state = {}
        self._device_display = {}
        self._cached_playlist = []

        # TODO: Add support for service name not just var name.
        self._state_var_handlers: StateVarHandlers = {
            StateVarName("LastChange"): self._last_change_event_handler,
            StateVarName("IdArray"): self._id_array_event_handler,
            StateVarName("CurrentPlaylistTrackID"): self._current_playlist_track_id_event_handler,
        }

        self._disconnected = False
        self._media_device: Optional[MediaSource] = None
        self._instance_id = 0  # CXNv2 implements a static AVTransport instance
        self._navigator_id = None
        self._subscriptions: ServiceSubscriptions = {}
        self._subscription_renewal_thread = None
        self._websocket_thread = None
        self._websocket_timeout = 1

        self._uu_vol_control = device.UuVolControl
        self._av_transport = device.AVTransport
        self._playlist_extension = device.PlaylistExtension

        self._subscribed_services = [
            self._av_transport,
            self._playlist_extension,
            self._uu_vol_control,
        ]

        ET.register_namespace("", "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/")
        ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
        ET.register_namespace("upnp", "urn:schemas-upnp-org:metadata-1-0/upnp/")
        ET.register_namespace("dlna", "urn:schemas-dlna-org:metadata-1-0/")

        # Configure navigator.
        nav_check = device.UuVolControl.IsRegisteredNavigatorName(
            NavigatorName=self.navigator_name
        )

        if nav_check["IsRegistered"]:
            self._navigator_id = nav_check["RetNavigatorId"]
        else:
            try:
                new_nav = device.UuVolControl.RegisterNamedNavigator(
                    NewNavigatorName=self.navigator_name
                )
                self._navigator_id = new_nav["RetNavigatorId"]
            except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
                logger.error(
                    "Could not acquire CXNv2 navigator. If device is in " +
                    "standby, power it on and try again."
                )
                raise VibinDeviceError(f"Could not acquire CXNv2 navigator: {e}")

        atexit.register(self.disconnect)

        # Determine audio sources
        try:
            response = requests.get(
                f"http://{self._device_hostname}/smoip/system/sources"
            )

            self._vibin_vars["audio_sources"] = response.json()["data"]["sources"]

            # sources = device.UuVolControl.GetAudioSourcesByNumber()
            # for source_number in sources["RetAudioSourceListValue"].split(","):
            #     source_int = int(source_number)
            #
            #     source_name = (
            #         device.UuVolControl.GetAudioSourceName(
            #             InAudioSource=source_int
            #         )
            #     )
            #
            #     self._vibin_vars["audio_sources"][source_int] = (
            #         source_name["RetAudioSourceName"]
            #     )
        except Exception:
            # TODO
            pass

        # THIS HAS BEEN REPLACED BY NOW_PLAYING INFO FROM SMOIP
        # # Current audio source.
        # try:
        #     current_source = device.UuVolControl.GetAudioSourceByNumber()
        #     self._set_current_audio_source(
        #         int(current_source["RetAudioSourceValue"])
        #     )
        # except Exception:
        #     # TODO
        #     pass

        # Current playlist.
        self._playlist_id_array = None
        self._set_current_playlist()

        # Current playlist track index.
        try:
            response = device.UuVolControl.GetCurrentPlaylistTrack()
            self._set_current_playlist_track_index(
                response["CurrentPlaylistTrackID"]
            )
        except Exception:
            # TODO
            pass

        if self._updates_handler:
            self._websocket_thread = StoppableThread(
                target=self._handle_websocket_to_streamer
            )
            self._websocket_thread.start()

        # Keep track of last seen ("currently playing") track and album IDs.
        # This is done to facilitate injecting this information into payloads
        # which want it, but it isn't already there when sent from the streamer.
        self._last_seen_track_id = None
        self._last_seen_album_id = None

    @property
    def device(self):
        return self._device

    def disconnect(self):
        if self._disconnected:
            return

        logger.info("CXNv2 disconnect requested")
        self._disconnected = True

        # Clean up the navigator.
        try:
            logger.info("Releasing navigator")
            self._uu_vol_control.ReleaseNavigator(NavigatorId=self._navigator_id)
        except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
            logger.error(f"Could not release navigator: {e}")

        # Clean up any UPnP subscriptions.
        self._cancel_subscriptions()

        if self._subscription_renewal_thread:
            logger.info("Stopping subscription renewal thread")
            self._subscription_renewal_thread.stop()
            self._subscription_renewal_thread.join()

        if self._websocket_thread:
            logger.info("Stopping streamer Websocket thread")
            self._websocket_thread.stop()
            self._websocket_thread.join()

        logger.info("CXNv2 disconnection complete")

    def register_media_source(self, media_source: MediaSource):
        self._media_device = media_source

    @property
    def name(self):
        return self._device.friendly_name

    @property
    def subscriptions(self) -> ServiceSubscriptions:
        return self._subscriptions

    def power_toggle(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/system/power?power=toggle"
        )

    def set_source(self, source: str):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/state?source={source}"
        )

    def ignore_playlist_updates(self, ignore=False):
        self._ignore_playlist_updates = ignore

    # TODO: Consider renaming to modify_playlist() or similar
    def play_metadata(
            self,
            metadata: str,
            action: str = "REPLACE",
            insert_index: Optional[int] = None,  # Only used by INSERT action
    ):
        try:
            if action == "INSERT":
                # INSERT. This works for Tracks only (not Albums).
                # TODO: Add check to ensure metadata is for a Track.
                self._uu_vol_control.InsertPlaylistTrack(
                    InsertPosition=insert_index, TrackData=metadata
                )
            else:
                # REPLACE, PLAY_NOW, PLAY_NEXT, PLAY_FROM_HERE, APPEND
                self._uu_vol_control.QueueFolder(
                    ServerUDN=self._media_device.udn,
                    Action=action,
                    NavigatorId=self._navigator_id,
                    ExtraInfo="",
                    DIDL=metadata,
                )
        except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
            # TODO: Look at using VibinDeviceError wherever things like
            #  _uu_vol_control are being used.
            raise VibinDeviceError(e)

    def play_playlist_index(self, index: int):
        self._uu_vol_control.SetCurrentPlaylistTrack(
            CurrentPlaylistTrackID=index
        )

    def play_playlist_id(self, playlist_id: int):
        try:
            playlist_index = self._playlist_array().index(playlist_id)
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

    def play(self):
        self._av_transport.Play(InstanceID=self._instance_id, Speed="1")

    def pause(self):
        self._av_transport.Pause(InstanceID=self._instance_id)

    def stop(self):
        self._av_transport.Stop(InstanceID=self._instance_id)

    def seek(self, target: SeekTarget):
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

                target_hmmss = utils.secs_to_hmmss(
                    math.floor(duration_secs * target)
                )
            else:
                target_hmmss = utils.secs_to_hmmss(int(target))
        elif isinstance(target, str):
            if not utils.is_hmmss(target):
                raise TypeError("Time must be in h:mm:ss format")

            target_hmmss = target

        if target_hmmss:
            self._av_transport.Seek(
                InstanceID=self._instance_id,
                Unit="ABS_TIME",
                Target=target_hmmss,
            )
        else:
            logger.warning(f"Unable to seek to {target}")

    def playlist_length(self):
        return self._uu_vol_control.GetPlaylistLength()["PlaylistLength"]

    # TODO: Clear up confusion between this track id (a playlist id) and the
    #   unique media id for the track.
    def current_track_id(self):
        # Appears to be the same as GetMediaQueueIndex()
        result = self._uu_vol_control.GetCurrentPlaylistTrack()

        return result["CurrentPlaylistTrackID"]

    def next_track(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?skip_track=1"
        )

        # ---------------------------------------------------------------------
        # TODO: Add support for airplay next/prev:
        #   http://10.0.0.13/smoip/zone/play_control?skip_track=1
        #   http://10.0.0.13/smoip/zone/play_control?skip_track=-1
        #   http://10.0.0.13/smoip/zone/play_state
        #   http://10.0.0.13/smoip/zone/play_state/position
        #
        # UuVolControl state: AudioSourceNumber determines AirPlay, etc
        # ---------------------------------------------------------------------

        # CXNv2's Next feature is not via AVTransport.Next(), but is instead
        # achieved via UuVolControl.SetCurrentPlaylistTrack().
        #
        # When already at the end of the playlist, Next will be a no-op unless
        # shuffle is enabled, in which case Next cycles back to track id 0.
        # a no-op; even if repeat is enabled.

        # current_track_id = self.current_track_id()
        # max_playlist_track_id = self.playlist_length() - 1
        #
        # if current_track_id < max_playlist_track_id:
        #     self._uu_vol_control.SetCurrentPlaylistTrack(
        #         CurrentPlaylistTrackID=(current_track_id + 1)
        #     )
        # elif self.repeat():
        #     self._uu_vol_control.SetCurrentPlaylistTrack(
        #         CurrentPlaylistTrackID=0
        #     )

    def previous_track(self):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?skip_track=-1"
        )

        # CXNv2's Previous feature is not via AVTransport.Previous(), but is
        # instead achieved via UuVolControl.SetCurrentPlaylistTrack().
        #
        # When already at the beginning of the playlist, Previous appears to
        # effectively restart the track; even if repeat is enabled.

        # current_track_id = self.current_track_id()
        #
        # if current_track_id > 0:
        #     self._uu_vol_control.SetCurrentPlaylistTrack(
        #         CurrentPlaylistTrackID=(current_track_id - 1)
        #     )
        # else:
        #     self.seek("0:00:00")

    def repeat(self, state: Optional[str] = "toggle"):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?mode_repeat={state}"
        )

        return self._playlist_extension.Repeat()

        # if enabled is None:
        #     result = self._playlist_extension.Repeat()
        #     return result["aRepeat"] == "true"
        #
        # self._playlist_extension.SetRepeat(
        #     aRepeat="true" if enabled else "false"
        # )
        #
        # return enabled

    def shuffle(self, state: Optional[str] = "toggle"):
        requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_control?mode_shuffle={state}"
        )

        return self._playlist_extension.Shuffle()

        # if enabled is None:
        #     result = self._playlist_extension.Shuffle()
        #     return result["aShuffle"] == "true"
        #
        # self._playlist_extension.SetShuffle(
        #     aShuffle="true" if enabled else "false"
        # )
        #
        # return enabled

    def transport_position(self) -> Optional[int]:
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/play_state/position"
        )

        if response.status_code != 200:
            return None

        try:
            return response.json()["data"]["position"]
        except (KeyError, json.decoder.JSONDecodeError) as e:
            return None

    def transport_actions(self):
        actions = self._av_transport.GetCurrentTransportActions(
            InstanceID=self._instance_id
        )

        return [action.lower() for action in actions["Actions"].split(", ")]

    def _transform_active_controls(self, controls):
        transform_map = {
            "pause": "pause",
            "play_pause": "stop",
            "toggle_shuffle": "shuffle",
            "toggle_repeat": "repeat",
            "track_next": "next",
            "track_previous": "previous",
            "seek": "seek",
        }

        transformed = []

        for control in controls:
            try:
                transformed.append(transform_map[control])
            except KeyError:
                transformed.append(control)

        return transformed

    def transport_active_controls(self):
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/now_playing"
        )

        # TODO: Improve error handling
        if response.status_code != 200:
            return []

        try:
            return self._transform_active_controls(
                response.json()["data"]["controls"]
            )
        except (KeyError, json.decoder.JSONDecodeError) as e:
            return []

    def transport_state(self) -> TransportState:
        info = self._av_transport.GetTransportInfo(InstanceID=self._instance_id)
        state = info["CurrentTransportState"]

        state_map = {
            "PAUSED_PLAYBACK": TransportState.PAUSED,
            "STOPPED": TransportState.STOPPED,
            "PLAYING": TransportState.PLAYING,
            "TRANSITIONING": TransportState.TRANSITIONING,
        }

        try:
            return state_map[state]
        except KeyError:
            return TransportState.UNKNOWN

    def transport_status(self) -> str:
        info = self._av_transport.GetTransportInfo(InstanceID=self._instance_id)

        return info["CurrentTransportStatus"]

    def _playlist_array(self) -> List[int]:
        try:
            response = self._device.PlaylistExtension.IdArray()
            playlist_encoded = response["aIdArray"]
            playlist_decoded = base64.b64decode(playlist_encoded)
            playlist_array = array.array("I", playlist_decoded)

            if sys.byteorder == "little":
                playlist_array.byteswap()

            return playlist_array
        except Exception:
            # TODO
            return []

    # TODO: Define PlaylistEntry and Playlist types
    def playlist(self):
        playlist_entry_ids = self._playlist_array()

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

            for (key, tag) in key_tag_map.items():
                entry_data[key] = item_elem .findtext(tag, namespaces=ns)

            entry_data["duration"] = (
                item_elem.find("res", namespaces=ns).attrib["duration"]
            )

            this_album_id, this_track_id = \
                self._album_and_track_ids_from_file(uri)

            entry_data["albumMediaId"] = this_album_id
            entry_data["trackMediaId"] = this_track_id

            entry_id_to_playlist_entry[id] = entry_data

        results = []

        for playlist_entry_id in playlist_entry_ids:
            try:
                results.append(entry_id_to_playlist_entry[playlist_entry_id])
            except KeyError:
                pass

        cached_playlist_media_ids = \
            [entry["trackMediaId"] for entry in self._cached_playlist]
        active_playlist_media_ids = \
            [entry["trackMediaId"] for entry in results]

        if cached_playlist_media_ids != active_playlist_media_ids:
            # NOTE: All changes to the active playlist should be detected here,
            #   regardless of where they originated (a Vibin client, another
            #   app like the StreamMagic iOS app, etc).
            self._on_playlist_modified(results)

        self._cached_playlist = results

        return results

    def _handle_websocket_to_streamer(self):
        async def async_websocket_manager():
            uri = f"ws://{self._device_hostname}:80/smoip"
            logger.info(f"Connecting to {self.name} Websocket server on {uri}")

            async with websockets.connect(
                uri,
                ssl=None,
                extra_headers={
                    "Origin": "vibin",
                }
            ) as websocket:
                logger.info(
                    f"Successfully connected to {self.name} Websocket server"
                )

                # Request playhead position updates (these arrive one per sec).
                await websocket.send(
                    '{"path": "/zone/play_state/position", "params": {"update": 1}}'
                )

                # Request now-playing updates, so the "controls" information can
                # be used to construct ActiveTransportControls messages.
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

                # TODO: Add /smoip/zone/now_playing
                #   Extract data.controls
                #   Use to set vibin-level "enabled controls"
                #
                # {
                #   "zone": "ZONE1",
                #   "data": {
                #     "state": "PLAYING",
                #     "source": {
                #       "id": "MEDIA_PLAYER",
                #       "name": "Media Library"
                #     },
                #     "display": {
                #       "line1": "Priority Boredom",
                #       "mqa": "none",
                #       "playback_source": "Asset UPnP: thicc",
                #       "class": "stream.media.upnp",
                #       "art_url": "http://192.168.1.14:26125/aa/538396257135550/cover.jpg?size=0",
                #       "context": "1/12"
                #     },
                #     "queue": {
                #       "length": 12,
                #       "position": 0,
                #       "shuffle": "off",
                #       "repeat": "off"
                #     },
                #     "controls": [
                #       "toggle_shuffle",
                #       "toggle_repeat",
                #       "track_next",
                #       "track_previous"
                #     ]
                #   }
                # }
                #
                # ALBUM PLAY:
                # "controls": [
                #   "pause",
                #   "play_pause",
                #   "toggle_shuffle",
                #   "toggle_repeat",
                #   "track_next",
                #   "track_previous",
                #   "seek"
                # ]
                #
                # PLAYLIST JUST SELECTED:
                # "controls": [
                #   "toggle_shuffle",
                #   "toggle_repeat",
                #   "track_next",
                #   "track_previous"
                # ]
                #
                # RADIO:
                # "controls": [
                #   "play_pause"
                # ]

                wait_for_message = True

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
                        update_dict = json.loads(update)

                        if update_dict["path"] == "/zone/play_state":
                            self._play_state = update_dict

                            # When the CXNv2 comes out of standby mode, its
                            # play_state update message does not include the
                            # following fields:
                            #
                            # If this play_state update comes in while the
                            # player is paused and the title matches playlist
                            # title for the queue_index, then we fill in some
                            # of the missing fields by taking their values from
                            # the matching playlist entry. This isn't ideal.
                            #
                            # TODO: Is there some way to ensure the play_state
                            #   message always includes all of the same fields
                            #   so we don't have to look elsewhere for any
                            #   missing values?

                            try:
                                play_state_data = self._play_state["params"]["data"]
                                play_state_metadata = play_state_data["metadata"]
                                play_state_queue_index = play_state_data["queue_index"]
                                play_state_title = play_state_metadata["title"]
                                queue_entry = self._cached_playlist[play_state_queue_index]

                                if (
                                    play_state_data["state"] == "pause" and
                                    queue_entry["title"] == play_state_title
                                ):
                                    if "album" not in play_state_metadata:
                                        play_state_metadata["album"] = queue_entry["album"]
                                    if "artist" not in play_state_metadata:
                                        play_state_metadata["artist"] = queue_entry["artist"]
                                    if "duration" not in play_state_metadata:
                                        play_state_metadata["duration"] = \
                                            utils.hmmss_to_secs(queue_entry["duration"])
                            except (IndexError, KeyError) as e:
                                pass

                            self._send_play_state_update()
                        elif update_dict["path"] == "/zone/play_state/position":
                            self._updates_handler("Position", update)
                        elif update_dict["path"] == "/zone/now_playing":
                            # TODO: now_playing is driving 3 chunks of data.
                            #   Figure out how to generalize this to not be
                            #   so specific to CXNv2/StreamMagic.

                            self._updates_handler(
                                "ActiveTransportControls",
                                json.dumps(self._transform_active_controls(
                                    update_dict["params"]["data"]["controls"]
                                ))
                            )

                            # TODO: Figure out what to do with current audio
                            #   source. This call to _set_current_audio_source
                            #   will ensure the source is set for the next
                            #   StateVars message publish.
                            audio_source_id = update_dict["params"]["data"]["source"]["id"]

                            self._set_current_audio_source(audio_source_id)

                            # Media IDs should only be sent to any clients when
                            # the current source is a MEDIA_PLAYER.
                            if audio_source_id != "MEDIA_PLAYER":
                                self._last_seen_track_id = None
                                self._last_seen_album_id = None

                            # TODO: Figure out what "display" means for other
                            #   streamer types.
                            try:
                                display_info = update_dict["params"]["data"]["display"]

                                if DeepDiff(display_info, self._device_display) != {}:
                                    self._device_display = display_info
                                    self._updates_handler(
                                        "DeviceDisplay",
                                        json.dumps(self._device_display)
                                    )
                            except KeyError:
                                pass
                        elif update_dict["path"] == "/presets/list":
                            self._updates_handler(
                                "Presets",
                                json.dumps(update_dict["params"]["data"])
                            )
                        elif update_dict["path"] == "/system/power":
                            power = update_dict["params"]["data"]["power"]
                            self._system_state["power"] = "on" if power == "ON" else "off"
                            self._updates_handler("System", json.dumps(self._system_state))
                        else:
                            logger.warning(f"Unknown message: {update}")
                            self._updates_handler("Unknown", update)
                    except (KeyError, json.decoder.JSONDecodeError) as e:
                        # TODO: This currently quietly ignores unexpected
                        #   payload formats or missing keys. Consider adding
                        #   error handling if errors need to be announced.
                        pass

        asyncio.run(async_websocket_manager())

    def _send_play_state_update(self):
        # NOTE: This manually injects the track and album media IDs into the
        # PlayState payload. The idea is that these IDs are part of the
        # "currently-playing" information, but the CXNv2 doesn't appear to
        # include this information itself, so it's injected here to meet what
        # might become the future payload contract for the PlayState type
        # coming from anything implementing Streamer.

        try:
            self._play_state["params"]["data"]["metadata"]["current_track_media_id"] = \
                self._last_seen_track_id
            self._play_state["params"]["data"]["metadata"]["current_album_media_id"] = \
                self._last_seen_album_id
        except KeyError:
            pass

        self._updates_handler("PlayState", json.dumps(self._play_state))

    def _send_device_display_update(self):
        self._updates_handler("DeviceDisplay", json.dumps(self._device_display))

    def _send_stored_playlists_update(self):
        self._updates_handler("StoredPlaylists", json.dumps(self._stored_playlists))

    def _renew_subscriptions(self):
        renewal_buffer = 10

        while not self._subscription_renewal_thread.stop_event.is_set():
            time.sleep(1)

            for service, subscription in self._subscriptions.items():
                now = int(time.time())

                if (
                    (subscription.timeout is not None)
                    and (now > (subscription.next_renewal - renewal_buffer))
                ):
                    logger.info(
                        f"Renewing UPnP subscription for {service.name}"
                    )

                    try:
                        timeout = service.renew_subscription(subscription.id)
                        subscription.timeout = timeout
                        subscription.next_renewal = (
                            (now + timeout) if timeout else None
                        )
                    except HTTPError:
                        logger.warning(
                            "Could not renew UPnP subscription. Attempting " +
                            "re-subscribe of all subscriptions."
                        )
                        # TODO: This is the renewal thread, but subscribe()
                        #   attempts to stop the thread; and can't join itself.
                        self.subscribe()

    def subscription_state_vars(self):
        return {
            subscribed_service.name: subscribed_service.statevars
            for subscribed_service in self._subscribed_services
        }

    def subscribe(self):
        # Clean up any existing subscriptions before making new ones.
        self._cancel_subscriptions()

        if self._subscribe_callback_base:
            for service in self._subscribed_services:
                now = int(time.time())
                (subscription_id, timeout) = service.subscribe(callback_url=(
                    f"{self._subscribe_callback_base}/{service.name}"
                ))

                self._subscriptions[service] = Subscription(
                    id=subscription_id,
                    timeout=timeout,
                    next_renewal=(now + timeout) if timeout else None
                )

                logger.info(
                    f"Subscribed to UPnP events from {service.name} with " +
                    f"timeout {timeout}"
                )

            if self._subscription_renewal_thread:
                logger.warning(
                    "Stopping UPnP subscription renewal thread"
                )

                try:
                    self._subscription_renewal_thread.stop()
                    self._subscription_renewal_thread.join()
                except RuntimeError as e:
                    logger.warning(
                        f"Cannot stop UPnP subscription renewal thread: {e}"
                    )
                finally:
                    self._subscription_renewal_thread = None

            self._subscription_renewal_thread = StoppableThread(
                target=self._renew_subscriptions
            )

            # TODO: This seems to be invoked multiple times
            logger.info("Starting UPnP subscription renewal thread")
            self._subscription_renewal_thread.start()

    def _cancel_subscriptions(self):
        # Clean up any UPnP subscriptions.
        for (service, subscription) in self._subscriptions.items():
            try:
                logger.info(f"Canceling UPnP subscription for {service.name}")
                service.cancel_subscription(subscription.id)
            except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
                logger.error(
                    f"Could not cancel UPnP subscription for {service.name}: {e}"
                )
            except HTTPError as e:
                if e.response.status_code == 412:
                    logger.warning(
                        f"Could not unsubscribe from {service.name} events; " +
                        f"subscription appears to have expired"
                    )
                else:
                    raise

        self._subscriptions = {}

    @property
    def system_state(self):
        return self._system_state

    @property
    def state_vars(self) -> StateVars:
        return self._state_vars

    @property
    def vibin_vars(self):
        return self._vibin_vars

    @property
    def play_state(self):
        # TODO: This is a raw CXNv2 Websocket payload shape. This should
        #   probably be cleaned up before passing back to the streamer-agnostic
        #   caller.
        return self._play_state

    @property
    def device_display(self):
        # TODO: This is a raw CXNv2 Websocket payload shape. This should
        #   probably be cleaned up before passing back to the streamer-agnostic
        #   caller.
        return self._device_display

    @property
    def presets(self):
        # TODO: Change to local cache data, as received from websocket.
        response = requests.get(
            f"http://{self._device_hostname}/smoip/presets/list"
        )

        return response.json()["data"]

    def play_preset_id(self, preset_id: int):
        response = requests.get(
            f"http://{self._device_hostname}/smoip/zone/recall_preset?preset={preset_id}"
        )

    def _last_change_event_handler(
            self, service_name: ServiceName, element: etree.Element,
    ):
        nested_element = etree.fromstring(element.text)
        instance_element = nested_element.find(
            "InstanceID", namespaces=nested_element.nsmap
        )

        result = {}

        for parameter in instance_element:
            param_name = etree.QName(parameter)

            try:
                _, marshaled_value = marshal_value(
                    self._device[service_name].statevars[param_name.localname][
                        "datatype"],
                    parameter.get("val"),
                )

                result[param_name.localname] = marshaled_value
            except KeyError:
                # TODO: Log
                pass

        return result

    def _id_array_event_handler(
            self, service_name: ServiceName, element: etree.Element
    ):
        if service_name == "PlaylistExtension":
            id_array = element.text

            if id_array != self._playlist_id_array:
                self._playlist_id_array = id_array
                self._set_current_playlist()

    def _current_playlist_track_id_event_handler(
            self, service_name: ServiceName, element: etree.Element
    ):
        self._set_current_playlist_track_index(int(element.text))

    def set_state_var(
            self,
            service_name: ServiceName,
            state_var_name: StateVarName,
            state_var_element: etree.Element
    ):
        if service_name not in self._state_vars:
            self._state_vars[service_name] = {}

        if state_var_handler := self._state_var_handlers.get(state_var_name):
            self._state_vars[service_name][state_var_name] = state_var_handler(
                service_name, state_var_element
            )
        else:
            _, marshaled_value = marshal_value(
                self._device[service_name].statevars[state_var_name]["datatype"],
                state_var_element.text,
            )

            self._state_vars[service_name][state_var_name] = marshaled_value

        # For each state var which contains XML text (i.e. any field name ending
        # in "XML"), we attempt to create a JSON equivalent.
        if state_var_name.endswith("XML"):
            json_var_name = f"{state_var_name[0:-3]}JSON"
            xml = state_var_element.text

            if xml:
                # TODO: This is not scalable (but html.escape also escapes tags)
                xml = xml.replace("&", "&amp;")

                try:
                    self._state_vars[service_name][json_var_name] = xmltodict.parse(xml)
                except xml.parsers.expat.ExpatError as e:
                    logger.error(
                        f"Could not convert XML to JSON for " +
                        f"{service_name}:{state_var_name}: {e}"
                    )

    def set_vibin_state_vars(self):
        # THIS HAS BEEN REPLACED BY NOW_PLAYING INFO FROM SMOIP
        # try:
        #     self._set_current_audio_source(
        #         int(self._state_vars["UuVolControl"]["AudioSourceNumber"])
        #     )
        # except KeyError:
        #     pass

        try:
            self._set_current_playback_details(
                self._state_vars["UuVolControl"]["PlaybackJSON"]["reciva"]["playback-details"]
            )
        except KeyError:
            pass

    def _set_current_audio_source(self, source_id: str):
        try:
            self._vibin_vars["current_audio_source"] = [
                source for source in self._vibin_vars["audio_sources"]
                if source["id"] == source_id
            ][0]
        except (IndexError, KeyError):
            self._vibin_vars["current_audio_source"] = None
            logger.warning(
                f"Could not determine current audio source from id '{source_id}', setting to None"
            )

    def _set_current_playlist(self):
        try:
            self._vibin_vars["current_playlist"] = self.playlist()
        except KeyError:
            pass

    def _set_current_playlist_track_index(self, index: int):
        try:
            self._vibin_vars["current_playlist_track_index"] = index
        except KeyError:
            pass

    def _album_and_track_ids_from_file(self, file) -> (Optional[str], Optional[str]):
        filename_only = pathlib.Path(file).stem

        # The streamed filename matches "<track>-<album>.ext". It seems
        # that the track id can itself include a hyphen whereas the album
        # id won't (TODO: can that be validated?).
        match = re.match(r"^(.*-([^-]+))$", filename_only)

        if match and len(match.groups(0)) == 2:
            this_track_id = match.groups(0)[0]
            this_album_id = match.groups(0)[1]

            return this_album_id, this_track_id

        return None, None

    def _set_current_playback_details(self, details):
        self._vibin_vars["current_playback_details"] = details

        # If the current playback details includes the streamed filename, then
        # extract the Track ID and Album ID.
        try:
            stream_url = details["stream"]["url"]
            this_album_id, this_track_id = \
                self._album_and_track_ids_from_file(stream_url)

            send_update = True

            if this_album_id and this_track_id:
                if this_track_id != self._last_seen_track_id:
                    self._last_seen_track_id = this_track_id
                    self._last_seen_album_id = this_album_id
                else:
                    send_update = False
            else:
                # A streamed file was found, but the track and album ids could
                # not be determined. Play it safe and set the IDs to None to
                # ensure clients aren't provided incorrect/misleading ids.
                self._last_seen_track_id = None
                self._last_seen_album_id = None

            # Force a send of a PlayState message to ensure any listening
            # clients get the new track/album id information without having
            # to wait for a normal PlayState update.
            if send_update:
                self._send_play_state_update()
        except KeyError:
            # No streamed filename found in the playback details
            pass

    def on_event(self, service_name: ServiceName, event: str):
        logger.debug(f"{self.name} received {service_name} event:\n\n{event}\n")
        # print(f"{self.name} received {service_name} event:\n\n{event}\n")

        property_set = etree.fromstring(event)

        for property in property_set:
            state_var_element = property[0]
            state_var_name = state_var_element.tag
            self.set_state_var(service_name, state_var_name, state_var_element)

        self.set_vibin_state_vars()
