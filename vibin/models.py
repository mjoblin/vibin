from dataclasses import dataclass

# TODO: Add a Container class?


@dataclass
class Album:
    id: str
    title: str
    creator: str
    date: str
    artist: str
    genre: str
    album_art_uri: str


@dataclass
class Track:
    id: str
    title: str
    creator: str
    date: str
    artist: str
    album: str
    duration: str
    genre: str
    album_art_uri: str
    original_track_number: str
