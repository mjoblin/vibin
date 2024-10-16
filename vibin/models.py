from typing import Any, Callable

from pydantic import BaseModel, Field
import upnpclient

from vibin.types import (
    AmplifierAction,
    FavoriteType,
    MediaId,
    MuteState,
    PlaylistModifyAction,
    PlayStatus,
    PowerState,
    TransportAction,
    TransportPosition,
    TransportRepeatState,
    TransportShuffleState,
    UpdateMessageType,
    UPnPProperties,
)


# -----------------------------------------------------------------------------
# Application models
#
# NOTE: Although Vibin wants to be fairly streamer and media server agnostic,
#   some of these models leak the data structure shapes found in the
#   StreamMagic and Asset implementations. If other streamers or media servers
#   were to be supported then that would likely require a refactoring of many
#   of these models.
# -----------------------------------------------------------------------------

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

    vibin_version: str
    start_time: float
    system_node: str
    system_platform: str
    system_version: str
    clients: list[WebSocketClientDetails]


# System (top-level streamer and media server) --------------------------------


class AudioSource(BaseModel):
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


class AudioSources(BaseModel):
    """All streamer audio sources, and which one is currently active."""

    available: list[AudioSource] = []
    active: AudioSource | None


class StreamerDeviceDisplayProgress(BaseModel):
    """Position into the current playlist entry, as shown on the streamer's display."""

    position: TransportPosition | None
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
    sources: AudioSources | None = AudioSources()
    display: StreamerDeviceDisplay | None = StreamerDeviceDisplay()


class MediaServerState(UPnPDeviceState):
    """Media server hardware state."""

    pass


class AmplifierState(UPnPDeviceState):
    """Amplifier hardware state."""

    supported_actions: list[AmplifierAction]
    power: PowerState | None
    mute: MuteState | None
    volume: float | None
    sources: AudioSources | None = AudioSources()


class SystemState(BaseModel):
    """System hardware state."""

    power: PowerState | None
    streamer: StreamerState
    media: MediaServerState | None
    amplifier: AmplifierState | None


class SystemUPnPProperties(BaseModel):
    """All UPnP properties for both the streamer and media server."""

    streamer: UPnPProperties
    media_server: UPnPProperties


# Media -----------------------------------------------------------------------


# TODO: MediaFolder is weird. Rethink how to manage folders, containers, and
#   general Media Server browsing, and MediaBrowseSingleLevel.
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


class MediaBrowseSingleLevel(BaseModel):
    """The single-level contents (no recursing) of an Id on the Media Server."""

    id: MediaId
    children: list[dict[str, Any]]


class Album(BaseModel):
    """An album on a local media server."""

    id: str | None
    title: str | None
    creator: str | None
    date: str | None
    artist: str | None
    genre: str | None
    album_art_uri: str | None


class Artist(BaseModel):
    """An artist on a local media server."""

    id: str | None
    title: str | None
    genre: str | None
    album_art_uri: str | None


class Track(BaseModel):
    """A track on a local media server."""

    id: str | None
    albumId: str | None
    title: str | None
    creator: str | None
    date: str | None
    artist: str | None
    # TODO: The "album" field cannot be None. This is done to ensure that
    #   pydantic will require an "album" field to be present for a dict to be
    #   interpreted as as a Track. If this isn't done then pydantic will coerce
    #   Tracks into Albums when generating a Favorite response. It would be
    #   preferred to figure out how to properly manage UPnP object types and
    #   not fall back on the "None" crutch for every field (Nones result in
    #   extremely tolerant coercions, which isn't always desirable).
    album: str
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


class TransportPlayheadPositionPayload(BaseModel):
    """Position into the current track (in whole seconds).

    This model exists for sending the playhead position over the wire, where
    a full class is preferred (it will be converted to a JSON object).
    """

    position: TransportPosition


# Streamer's active playlist --------------------------------------------------


class ActivePlaylistEntry(BaseModel):
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


class ActivePlaylist(BaseModel):
    """The streamer's active playlist."""

    current_track_index: int | None
    entries: list[ActivePlaylistEntry] = []


class ActivePlaylistModifyPayload(BaseModel):
    """A modification request to the streamer's active playlist."""

    action: PlaylistModifyAction
    max_count: int | None
    media_ids: list[MediaId]


PlaylistModifiedHandler = Callable[[list[ActivePlaylistEntry]], None]


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

    album_media_id: MediaId | None
    track_media_id: MediaId | None
    active_track: ActiveTrack = ActiveTrack()
    playlist: ActivePlaylist = ActivePlaylist()
    format: MediaFormat = MediaFormat()
    stream: MediaStream = MediaStream()


# Stored Playlists ------------------------------------------------------------

StoredPlaylistEntryId = str


class StoredPlaylist(BaseModel):
    id: str
    name: str
    created: float
    updated: float
    entry_ids: list[StoredPlaylistEntryId]


class StoredPlaylistStatus(BaseModel):
    active_id: str | None = None
    is_active_synced_with_store: bool = False
    is_activating_playlist: bool = False


class StoredPlaylists(BaseModel):
    status: StoredPlaylistStatus
    playlists: list[StoredPlaylist]


# Favorites -------------------------------------------------------------------


class Favorite(BaseModel):
    """A single stored favorite."""

    type: FavoriteType
    media_id: MediaId
    when_favorited: float | None
    media: Track | Album | None


Favorites = list[Favorite]


class FavoritesPayload(BaseModel):
    """A list of Favorites.

    This model exists for sending Favorites over the wire, where a full class
    is preferred (it will be converted to a JSON object).
    """

    favorites: Favorites


# Lyrics ----------------------------------------------------------------------


class LyricsChunk(BaseModel):
    """A single chunk of lyrics (usually a verse or chorus)."""

    header: str | None
    body: list[str] | None


# TODO: Consider renaming to PersistedLyrics
class Lyrics(BaseModel):
    """Lyrics for a single Media Id."""

    lyrics_id: str
    media_id: MediaId | None
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

    media_id: MediaId | None
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
