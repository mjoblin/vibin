from typing import Optional

from fastapi import APIRouter, HTTPException

from vibin.models import PlaylistModifyPayload
from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

playlist_router = APIRouter()


@playlist_router.get("/playlist", summary="", description="", tags=["Active Playlist"])
@transform_media_server_urls_if_proxying
def playlist():
    return get_vibin_instance().streamer.playlist()


@playlist_router.post(
    "/playlist/modify", summary="", description="", tags=["Active Playlist"]
)
def playlist_modify_multiple_entries(payload: PlaylistModifyPayload):
    if payload.action != "REPLACE":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action: {payload.action}. Supported actions: REPLACE.",
        )

    return get_vibin_instance().play_ids(payload.media_ids, max_count=payload.max_count)


@playlist_router.post(
    "/playlist/modify/{media_id}",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_modify_single_entry(
    media_id: str,
    action: str = "REPLACE",
    insert_index: Optional[int] = None,
):
    return get_vibin_instance().modify_playlist(media_id, action, insert_index)


@playlist_router.post(
    "/playlist/play/id/{playlist_entry_id}",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_play_id(playlist_entry_id: int):
    return get_vibin_instance().streamer.play_playlist_id(playlist_entry_id)


@playlist_router.post(
    "/playlist/play/index/{index}",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_play_index(index: int):
    return get_vibin_instance().streamer.play_playlist_index(index)


@playlist_router.post(
    "/playlist/play/favorites/albums",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_play_favorite_albums(max_count: int = 10):
    return get_vibin_instance().play_favorite_albums(max_count=max_count)


@playlist_router.post(
    "/playlist/play/favorites/tracks",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_play_favorite_tracks(max_count: int = 100):
    return get_vibin_instance().play_favorite_tracks(max_count=max_count)


@playlist_router.post(
    "/playlist/clear", summary="", description="", tags=["Active Playlist"]
)
def playlist_clear():
    return get_vibin_instance().streamer.playlist_clear()


@playlist_router.post(
    "/playlist/delete/{playlist_entry_id}",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_delete_entry(playlist_entry_id: int):
    return get_vibin_instance().streamer.playlist_delete_entry(playlist_entry_id)


@playlist_router.post(
    "/playlist/move/{playlist_entry_id}",
    summary="",
    description="",
    tags=["Active Playlist"],
)
def playlist_move_entry(playlist_entry_id: int, from_index: int, to_index: int):
    return get_vibin_instance().streamer.playlist_move_entry(
        playlist_entry_id, from_index, to_index
    )
