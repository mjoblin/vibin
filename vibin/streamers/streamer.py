from abc import ABCMeta, abstractmethod
from typing import Literal

import upnpclient

from vibin.mediaservers import MediaServer
from vibin.models import (
    ActivePlaylist,
    CurrentlyPlaying,
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
    UpdateMessageHandler,
    UPnPProperties,
)


# -----------------------------------------------------------------------------
# Streamer interface.
#
# This interface is to be implemented by any Vibin class managing a network
# music Streamer.
#
# The interface is strongly influenced by the StreamMagic implementation, which
# means it is likely a very leaky abstraction exposing many design choices of
# the StreamMagic product.
#
# Reference UPnP documentation:
#
# http://upnp.org/specs/av/UPnP-av-AVArchitecture-v2.pdf
# http://upnp.org/specs/av/UPnP-av-AVTransport-v3-Service.pdf
# -----------------------------------------------------------------------------


class Streamer(metaclass=ABCMeta):
    """
    Manage a network streamer for Vibin.

        * `device`: The `upnp.Device` instance for the streamer to be managed.
        * `upnp_subscription_callback_base`: The REST API base URL to use when
            subscribing to streamer-related UPnP service events. Events will be
            passed to the implementation's `on_upnp_event()`.
        * `on_update`: A callback to invoke when a message is ready to be sent
            back to Vibin.
        * `on_playlist_modified`: A callback to invoke when the streamer's
            active playlist has been modified.
    """

    model_name = "VibinStreamer"

    @abstractmethod
    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
        on_playlist_modified: PlaylistModifiedHandler | None = None,
    ):
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """The UPnP device name for the Streamer."""
        pass

    @property
    @abstractmethod
    def device(self) -> upnpclient.Device:
        """The UPnP device instance associated with the Streamer."""
        pass

    @property
    @abstractmethod
    def device_state(self) -> StreamerState:
        """System state for the Streamer."""
        pass

    @property
    @abstractmethod
    def device_udn(self) -> str:
        """The Streamer's UPnP device UDN (Unique Device Name)."""
        pass

    @property
    @abstractmethod
    def device_display(self) -> StreamerDeviceDisplay:
        """Return information shown on the Streamer's display."""
        pass

    @abstractmethod
    def register_media_server(self, media_server: MediaServer) -> None:
        """Register a MediaServer instance.

        Allows the Streamer to interact with Vibin's Media Server.
        """
        pass

    @abstractmethod
    def on_startup(self) -> None:
        """Called when the Vibin system has started up."""
        pass

    @abstractmethod
    def on_shutdown(self) -> None:
        """Called when the Vibin system is shut down."""
        pass

    # -------------------------------------------------------------------------
    # System

    @property
    @abstractmethod
    def power(self) -> PowerState:
        """Power state."""
        pass

    @power.setter
    @abstractmethod
    def power(self, state: PowerState) -> None:
        """Set the Streamer's power state."""
        pass

    @abstractmethod
    def power_toggle(self) -> None:
        """Toggle the Streamer's power state."""
        pass

    @property
    @abstractmethod
    def currently_playing(self) -> CurrentlyPlaying:
        pass

    @abstractmethod
    def set_audio_source(self, source: str) -> None:
        """Set the active Audio Source by name."""
        pass

    # -------------------------------------------------------------------------
    # Transport

    @abstractmethod
    def transport_state(self) -> TransportState:
        """The current transport state."""
        pass

    @abstractmethod
    def play(self):
        """Play (resume) playback."""
        pass

    @abstractmethod
    def toggle_playback(self):
        """Toggle the playback state."""
        pass

    @abstractmethod
    def pause(self):
        """Pause playback."""
        pass

    @abstractmethod
    def stop(self):
        """Stop playback."""
        pass

    @abstractmethod
    def seek(self, target: SeekTarget):
        """Seek into the currently-playing playlist entry.

        `target` can be:
            * float: `0.0` (beginning) to `1.0` (end) of playlist entry
            * int: number of whole seconds into the playlist entry
            * str: `"h:mm:ss"` into the playlist entry
        """
        pass

    @abstractmethod
    def next_track(self):
        """Play next entry in active playlist."""
        pass

    @abstractmethod
    def previous_track(self):
        """Return to beginning of current entry or play previous playlist entry.

        Behavior depends on the streamer. Will usually return to beginning of
        the current entry _unless_ called near the very beginning of entry
        playback, in which case the previous playlist entry will be played.
        """
        pass

    @abstractmethod
    def repeat(
        self, state: TransportRepeatState | Literal["toggle"]
    ) -> TransportRepeatState:
        """Set repeat state."""
        pass

    @abstractmethod
    def shuffle(
        self, state: TransportShuffleState | Literal["toggle"]
    ) -> TransportShuffleState:
        """Set shuffle state."""
        pass

    @property
    @abstractmethod
    def transport_position(self) -> TransportPosition:
        """The current transport position (duration into the current playlist
        entry), in whole seconds."""
        pass

    @property
    @abstractmethod
    def active_transport_controls(self) -> list[TransportAction]:
        """Transport controls which are currently available.

        The available transport controls will vary based on audio source and
        current transport state. For example, seek and next/previous will not
        be active when an Internet Radio station is being played.
        """
        pass

    # -------------------------------------------------------------------------
    # Active Playlist

    @property
    @abstractmethod
    def playlist(self) -> ActivePlaylist:
        """The current Active Playlist."""
        pass

    @abstractmethod
    def modify_playlist(
        self,
        metadata: str,
        action: PlaylistModifyAction = "REPLACE",
        insert_index: int | None = None,
    ):
        """Modify the active playlist.

        Modifying the playlist takes the media represented by `metadata` and
        applies one of the following `action`s:

         * `"APPEND"`: Append to the end of the playlist. (Track or Album).
         * `"INSERT"`: Insert into the playlist at location `insert_index`.
           (Track only).
         * `"PLAY_FROM_HERE"`: Replace the playlist with the Track's entire
           Album, and plays the Track. (Track only).
         * `"PLAY_NEXT"`: Insert into the playlist after the current entry.
           (Track or Album).
         * `"PLAY_NOW"`: Insert into the playlist at the current entry. (Track
           or Album).
         * `"REPLACE"`: Replace the playlist. (Track or Album).
        """
        pass

    @abstractmethod
    def play_playlist_index(self, index: int):
        """Play a playlist entry by index."""
        pass

    @abstractmethod
    def play_playlist_id(self, playlist_id: int):
        """Play a playlist entry by playlist entry ID."""
        pass

    @abstractmethod
    def playlist_clear(self):
        """Clear the playlist."""
        pass

    @abstractmethod
    def playlist_delete_entry(self, playlist_id: int):
        """Remove an entry from the playlist by entry ID."""
        pass

    @abstractmethod
    def playlist_move_entry(self, playlist_id: int, from_index: int, to_index: int):
        """Move a playlist entry to another index position in the playlist."""
        pass

    # -------------------------------------------------------------------------
    # Presets

    @property
    @abstractmethod
    def presets(self) -> Presets:
        """All Presets."""
        pass

    @abstractmethod
    def play_preset_id(self, preset_id: int):
        """Initiate playback of the given `preset_id`.

        For a preset like Internet Radio, the station will be played without
        altering the active playlist. For a preset like an Album or Track from
        the local media sever, initiating playback will replace the active
        playlist.
        """
        pass

    # -------------------------------------------------------------------------
    # UPnP

    @abstractmethod
    def subscribe_to_upnp_events(self) -> None:
        """Invoked when the Streamer should initiate any UPnP service event
        subscriptions."""
        pass

    @abstractmethod
    def upnp_properties(self) -> UPnPProperties:
        """All UPnP properties for the Streamer.

        Properties are only available for any UPnP service subscriptions
        managed by the Streamer implementation.
        """
        pass

    @property
    @abstractmethod
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        """All active UPnP subscriptions."""
        pass

    @abstractmethod
    def on_upnp_event(self, service_name: str, event: str):
        """Invoked when a UPnP event has been received from a subscription
        managed by the Streamer.
        """
        pass
