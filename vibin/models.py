from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, HttpUrl
import upnpclient

from vibin.types import (
    MediaId,
    PlaylistModifyAction,
    PlayStatus,
    PowerState,
    TransportAction,
    TransportPlayStatus,
    TransportRepeatState,
    TransportShuffleState,
    UpdateMessageType,
    UPnPProperties,
)


# Messaging -------------------------------------------------------------------


class UpdateMessage(BaseModel):
    """A message describing a single update."""

    message_type: UpdateMessageType
    payload: Any


class WebSocketClientDetails(BaseModel):
    """A single WebSocket client connection."""

    id: str
    when_connected: float
    ip: str
    port: int


# Vibin -----------------------------------------------------------------------


class VibinSettings(BaseModel):
    """Modifiable Vibin settings."""

    all_albums_path: str
    new_albums_path: str
    all_artists_path: str


class VibinStatus(BaseModel):
    """Vibin system status."""

    start_time: float
    system_node: str
    system_platform: str
    system_version: str
    clients: list[WebSocketClientDetails]


# System ----------------------------------------------------------------------


class MediaSource(BaseModel):
    """A source like "CD" or "Media Library"."""

    id: str | None
    name: str | None
    default_name: str | None
    class_field: str | None = Field(alias="class")
    nameable: bool | None
    ui_selectable: bool | None
    description: str | None
    description_locale: str | None
    preferred_order: int | None


class MediaSources(BaseModel):
    """All streamer sources, and which one is currently active."""

    available: list[MediaSource] = []
    active: MediaSource | None


class StreamerDeviceDisplayProgress(BaseModel):
    """Position into the current track, as shown on the streamer's display."""

    position: int | None
    duration: int | None


class StreamerDeviceDisplay(BaseModel):
    """Information displayed on a streamer's display screen."""

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


class UPnPDeviceState(BaseModel):
    """Information on a UPnP device."""

    name: str


class StreamerState(UPnPDeviceState):
    """Streamer hardware state."""

    power: PowerState | None
    sources: MediaSources | None = MediaSources()
    display: StreamerDeviceDisplay | None = StreamerDeviceDisplay()


class MediaServerState(UPnPDeviceState):
    """Media server hardware state."""

    pass


class SystemState(BaseModel):
    """System hardware state."""

    streamer: StreamerState
    media: MediaServerState


class SystemUPnPProperties(BaseModel):
    """All UPnP properties for both the streamer and media server."""

    streamer: UPnPProperties
    media_server: UPnPProperties


# Media -----------------------------------------------------------------------


class MediaFolder(BaseModel):
    """A folder on a local media server."""

    creator: str | None
    title: str | None
    album_art_uri: str | None
    artist: str | None
    class_field: str | None = Field(alias="class")
    genre: str | None

    class Config:
        allow_population_by_field_name = True


class Album(BaseModel):
    """An album on a local media server."""

    id: str | None
    parentId: str | None
    title: str | None
    creator: str | None
    date: str | None
    artist: str | None
    genre: str | None
    album_art_uri: str | None


class Artist(BaseModel):
    """An artist on a local media server."""

    id: str | None
    parentId: str | None
    title: str | None
    genre: str | None
    album_art_uri: str | None


class Track(BaseModel):
    """A track on a local media server."""

    id: str | None
    parentId: str | None
    title: str | None
    creator: str | None
    date: str | None
    artist: str | None
    album: str | None
    duration: str | None
    genre: str | None
    album_art_uri: str | None
    original_track_number: int | None


# Transport -------------------------------------------------------------------


class TransportState(BaseModel):
    """State of a streamer transport."""

    play_state: PlayStatus | None
    active_controls: list[TransportAction] = []
    repeat: TransportRepeatState | None
    shuffle: TransportShuffleState | None


class TransportPlayheadPosition(BaseModel):
    """Position into the current track (in whole seconds)."""

    position: int


class TransportActiveControls(BaseModel):
    """Transport actions which can currently be performed on the streamer."""

    active_controls: list[TransportAction]


# TODO: Deprecate along with TransportPlayState
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


# TODO: Remove once confirmed it's not in use (replaced by TransportState)
class TransportPlayState(BaseModel):
    state: TransportPlayStatus | None
    position: int | None
    # TODO: Consider renaming "queue_*" to "active_playlist_*", or just remove
    #   since they can be determined from the active playlist
    queue_index: int | None
    queue_length: int | None
    queue_id: int | None
    mode_repeat: TransportRepeatState | None
    mode_shuffle: TransportShuffleState | None
    metadata: TransportPlayStateMetadata | None


# Active Playlist -------------------------------------------------------------


class PlaylistEntry(BaseModel):
    """A single entry in the streamer's active playlist."""

    album: str | None
    albumArtURI: str | None
    artist: str | None
    duration: str | None
    genre: str | None
    id: int | None
    index: int | None
    originalTrackNumber: str | None
    title: str | None
    uri: str | None
    albumMediaId: str | None
    trackMediaId: str | None


class Playlist(BaseModel):
    """The streamer's active playlist."""

    current_track_index: int | None
    entries: list[PlaylistEntry] = []


class PlaylistModifyPayload(BaseModel):
    """A modification request to the streamer's active playlist."""

    action: PlaylistModifyAction
    max_count: int | None
    media_ids: list[MediaId]


# Currently playing -----------------------------------------------------------


class MediaFormat(BaseModel):
    """Media format details."""

    sample_format: str | None
    mqa: str | None
    codec: str | None
    lossless: bool | None
    sample_rate: int | None
    bit_depth: int | None
    encoding: str | None


class MediaStream(BaseModel):
    """Location of a media stream (e.g. a url to a .flac file)."""

    url: str | None


class ActiveTrack(BaseModel):
    """
    The currently-playing track on the streamer.

    This is different from a Track, which describes a track on the local media
    server. The ActiveTrack is more general, supporting both local media tracks
    and non-local tracks (such as AirPlay).
    """

    title: str | None
    artist: str | None
    album: str | None
    art_url: str | None
    duration: int | None


class CurrentlyPlaying(BaseModel):
    """The state of what is currently playing, track and playlist."""

    album_media_id: str | None
    track_media_id: str | None
    active_track: ActiveTrack = ActiveTrack()
    playlist: Playlist = Playlist()
    format: MediaFormat = MediaFormat()
    stream: MediaStream = MediaStream()


# Stored Playlists ------------------------------------------------------------

StoredPlaylistEntryId = str


@dataclass
class StoredPlaylist:
    id: str
    name: str
    created: float
    updated: float
    entry_ids: list[StoredPlaylistEntryId]


@dataclass
class StoredPlaylistStatus:
    active_id: str | None = None
    is_active_synced_with_store: bool = False
    is_activating_new_playlist: bool = False


# Favorites -------------------------------------------------------------------


class Favorite(BaseModel):
    """A single stored favorite."""

    type: str
    media_id: str
    when_favorited: float | None
    media: Album | Track | None = None


class Favorites(BaseModel):
    """All stored favorites."""

    favorites: list[Favorite]


# Lyrics ----------------------------------------------------------------------


class LyricsChunk(BaseModel):
    """A single chunk of lyrics (usually a verse or chorus)."""

    header: str | None
    body: list[str] | None


# TODO: Consider renaming to PersistedLyrics
class Lyrics(BaseModel):
    """Lyrics for a single Media Id."""

    lyrics_id: str
    media_id: str | None
    is_valid: bool
    chunks: list[LyricsChunk]


class LyricsQuery(BaseModel):
    """A lyrics query."""

    query: str


# External services -----------------------------------------------------------


class ExternalServiceLink(BaseModel):
    """A single link to an external service (e.g. Wikipedia)."""

    type: str
    name: str
    url: str


# TODO: Consider renaming to PersistedLinks
class Links(BaseModel):
    """All links to an external service for a Media Id."""

    media_id: str | None
    links: dict[str, list[ExternalServiceLink]]


# Presets ---------------------------------------------------------------------


class Preset(BaseModel):
    """A single streamer preset."""

    id: int | None
    name: str | None
    type: str | None
    class_field: str | None = Field(alias="class")
    state: str | None
    is_playing: bool | None
    art_url: str | None


class Presets(BaseModel):
    """All streamer presets."""

    start: int | None
    end: int | None
    max_presets: int | None
    presets: list[Preset] | None = []


# UPnP ------------------------------------------------------------------------


class UPnPSubscription(BaseModel):
    """A UPnP subscription to a single service on a device."""

    id: str
    timeout: int | None
    next_renewal: int | None


UPnPServiceSubscriptions = dict[upnpclient.Service, UPnPSubscription]
