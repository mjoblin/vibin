from typing import NewType, Callable, Any, Literal

from lxml import etree

# -----------------------------------------------------------------------------
# Application types
#
# NOTE: Although Vibin wants to be fairly streamer and media server agnostic,
#   some of these types leak the types found in the StreamMagic and Asset
#   implementations. If other streamers or media servers were to be supported
#   then that would likely require a refactoring of many of these types.
# -----------------------------------------------------------------------------

MediaId = str  # Local media server id (Album, Track, Artist)

PowerState = Literal["on", "off"]

# Modifications that can be made to the active streamer playlist
PlaylistModifyAction = Literal[
    "REPLACE", "PLAY_NOW", "PLAY_NEXT", "PLAY_FROM_HERE", "APPEND"
]

# Messaging -------------------------------------------------------------------

# Message types sent to subscribed clients (over a WebSocket)
UpdateMessageType = Literal[
    "ActiveTransportControls",  # TODO: Deprecate
    "CurrentlyPlaying",
    "DeviceDisplay",  # TODO: Deprecate
    "Favorites",
    "PlayState",  # TODO: Deprecate
    "Position",
    "Presets",
    "StoredPlaylists",
    "System",
    "TransportState",
    "UPnPProperties",
    "VibinStatus",
]

UpdateMessageHandler = Callable[[UpdateMessageType, Any], None]

# UPnP ------------------------------------------------------------------------

UPnPDeviceType = Literal["streamer", "media_server"]

UPnPServiceName = NewType("UPnPServiceName", str)

UPnPPropertyName = NewType("UPnPPropertyName", str)

UPnPProperties = dict[UPnPServiceName, dict[UPnPPropertyName, Any]]

UPnPPropertyChangeHandlers = dict[
    (UPnPServiceName, UPnPPropertyName), Callable[[UPnPServiceName, etree.Element], Any]
]

# Transport -------------------------------------------------------------------

# Transport play states.
PlayStatus = Literal[
    "buffering",
    "connecting",
    "no_signal",
    "not_ready",
    "pause",
    "play",
    "ready",
    "stop",
]

# Actions that can be performed on the streamer.
TransportAction = Literal[
    "next",
    "pause",
    "play",
    "previous",
    "repeat",
    "seek",
    "shuffle",
    "stop",
]

# TODO: Deprecate along with the TransportPlayState class
TransportPlayStatus = Literal[
    "buffering",
    "connecting",
    "no_signal",
    "not_ready",
    "pause",
    "play",
    "ready",
    "stop",
]

TransportRepeatState = Literal["off", "all"]

TransportShuffleState = Literal["off", "all"]

# Float: 0.0 -> 1.0 (for beginning -> end of track; 0.5 is half way into track)
# Int: Number of seconds into the track
# Str: h:mm:ss into the track
SeekTarget = float | int | str