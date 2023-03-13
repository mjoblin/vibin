from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

# TODO: Add a Container class?


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


@dataclass
class ExternalServiceLink:
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


@dataclass
class Preset:
    id: int
    name: str
    type: str
    className: str
    state: str
    is_playing: bool
    art_url: str
