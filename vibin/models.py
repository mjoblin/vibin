from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel


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


class PlaylistModifyPayload(BaseModel):
    action: str
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
