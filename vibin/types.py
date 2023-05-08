from typing import NewType, Callable, Any, Literal

from lxml import etree

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

UPnPDeviceType = Literal["streamer", "media"]

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
