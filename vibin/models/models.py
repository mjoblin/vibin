from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl
import upnpclient


# -----------------------------------------------------------------------------
# This file contains models which are used by multiple consumers across the
# application. Some models, which are used only in one location, are defined
# in that one location instead -- such as a single router file, along with the
# router's endpoint definitions.
# -----------------------------------------------------------------------------


# TODO: Add a Container class?


class VibinSettings(BaseModel):
    all_albums_path: str
    new_albums_path: str
    all_artists_path: str


@dataclass
class Album:
    id: str
    parentId: str
    title: str
    creator: str
    date: str
    artist: str
    genre: str
    album_art_uri: str


@dataclass
class Artist:
    id: str
    parentId: str
    title: str
    genre: str
    album_art_uri: str


@dataclass
class Track:
    id: str
    parentId: str
    title: str
    creator: str
    date: str
    artist: str
    album: str
    duration: str
    genre: str
    album_art_uri: str
    original_track_number: str


class ExternalServiceLink(BaseModel):
    type: str
    name: str
    url: str


StoredPlaylistEntryId = str


@dataclass
class StoredPlaylist:
    id: str
    name: str
    created: float
    updated: float
    entry_ids: list[StoredPlaylistEntryId]


class Favorite(BaseModel):
    type: str
    media_id: str
    when_favorited: Optional[float]


class LyricsChunk(BaseModel):
    header: Optional[str]
    body: Optional[list[str]]


# TODO: Consider renaming to PersistedLyrics
class Lyrics(BaseModel):
    lyrics_id: str
    media_id: Optional[str]
    is_valid: bool
    chunks: list[LyricsChunk]


class LyricsQuery(BaseModel):
    query: str


# TODO: Consider renaming to PersistedLinks
class Links(BaseModel):
    media_id: Optional[str]
    links: dict[str, list[ExternalServiceLink]]


@dataclass
class Preset:
    id: int
    name: str
    type: str
    className: str
    state: str
    is_playing: bool
    art_url: str


MediaId = str


@dataclass
class StoredPlaylistStatus:
    active_id: Optional[str] = None
    is_active_synced_with_store: bool = False
    is_activating_new_playlist: bool = False


PlaylistModifyAction = Literal[
    "REPLACE", "PLAY_NOW", "PLAY_NEXT", "PLAY_FROM_HERE", "APPEND"
]


class PlaylistModifyPayload(BaseModel):
    action: PlaylistModifyAction
    max_count: Optional[int]
    media_ids: list[MediaId]


class WebSocketClientDetails(BaseModel):
    id: str
    when_connected: float
    ip: str
    port: int


class ServerStatus(BaseModel):
    start_time: float
    system_node: str
    system_platform: str
    system_version: str
    clients: list[WebSocketClientDetails]


class TransportPlayheadPosition(BaseModel):
    position: int


TransportControl = Literal[
    "next",
    "pause",
    "play",
    "previous",
    "repeat",
    "seek",
    "shuffle",
    "stop",
]


class TransportActiveControls(BaseModel):
    active_controls: list[TransportControl]


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


class TransportPlayStateMetadata(BaseModel):
    class_field: str | None = Field(alias="class")
    source: str | None
    name: str | None
    playback_source: str | None
    track_number: int | None
    duration: int | None
    album: str | None
    artist: str | None
    title: str | None
    art_url: HttpUrl | None
    sample_format: str | None
    mqa: str | None
    codec: str | None
    lossless: bool | None
    sample_rate: int | None
    bit_depth: int | None
    encoding: str | None
    current_track_media_id: str | None
    current_album_media_id: str | None


class TransportPlayState(BaseModel):
    state: TransportPlayStatus | None
    position: int | None
    presettable: bool | None
    queue_index: int | None
    queue_length: int | None
    queue_id: int | None
    mode_repeat: TransportRepeatState | None
    mode_shuffle: TransportShuffleState | None
    metadata: TransportPlayStateMetadata | None


@dataclass
class Subscription:
    id: str
    timeout: int | None
    next_renewal: int | None


ServiceSubscriptions = dict[upnpclient.Service, Subscription]


# Models


class StreamerDeviceDisplayProgress(BaseModel):
    position: int | None
    duration: int | None


class StreamerDeviceDisplay(BaseModel):
    line1: str | None
    line2: str | None
    line3: str | None
    format: str | None
    mqa: str | None
    playback_source: str | None
    class_field: str | None = Field(alias="class")
    art_file: str | None
    art_url: str | None
    progress: StreamerDeviceDisplayProgress | None
    context: str | None


WebSocketMessageType = Literal[
    "ActiveTransportControls",
    "DeviceDisplay",
    "Favorites",
    "PlayState",
    "Position",
    "Presets",
    "StateVars",
    "StoredPlaylists",
    "System",
    "VibinStatus",
]


class WebSocketMessage(BaseModel):
    message_type: WebSocketMessageType
    message: Any


WebSocketMessageHandler = Callable[[WebSocketMessageType, Any], None]


UPnPDeviceType = Literal["streamer", "media"]
