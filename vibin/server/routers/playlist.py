from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin.models import PlaylistModifyAction, PlaylistModifyPayload
from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /playlist route.
# -----------------------------------------------------------------------------

playlist_router = APIRouter()


@playlist_router.get(
    "/playlist",
    summary="Retrieve details on the Streamer's active Playlist",
    tags=["Active Playlist"],
)
@transform_media_server_urls_if_proxying
def playlist():
    return get_vibin_instance().streamer.playlist()


@playlist_router.post(
    "/playlist/play/id/{playlist_entry_id}",
    summary="Play a Playlist Entry in the Streamer's active Playlist, by Playlist Entry ID",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_play_id(playlist_entry_id: int):
    get_vibin_instance().streamer.play_playlist_id(playlist_entry_id)


@playlist_router.post(
    "/playlist/play/index/{index}",
    summary="Play a Playlist Entry in the Streamer's active Playlist, by index",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_play_index(index: int):
    get_vibin_instance().streamer.play_playlist_index(index)


@playlist_router.post(
    "/playlist/play/favorites/albums",
    summary="Play Album favorites",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_play_favorite_albums(max_count: int = 10):
    get_vibin_instance().play_favorite_albums(max_count=max_count)


@playlist_router.post(
    "/playlist/play/favorites/tracks",
    summary="Play Track favorites",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_play_favorite_tracks(max_count: int = 100):
    get_vibin_instance().play_favorite_tracks(max_count=max_count)


@playlist_router.post(
    "/playlist/modify",
    summary="Modify the Streamer's active Playlist with multiple Media IDs",
    description=(
        "Currently, the only supported action is `REPLACE`, which replaces the "
        + "Streamer's active Playlist with the provided Media IDs."
    ),
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_modify_multiple_entries(payload: PlaylistModifyPayload):
    if payload.action != "REPLACE":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action: {payload.action}. Supported actions: REPLACE.",
        )

    get_vibin_instance().play_ids(payload.media_ids, max_count=payload.max_count)


@playlist_router.post(
    "/playlist/modify/{media_id}",
    summary="Modify the Streamer's active Playlist with a single Media ID",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_modify_single_entry(
    media_id: str,
    action: PlaylistModifyAction = "REPLACE",
    insert_index: Optional[int] = None,
):
    get_vibin_instance().modify_playlist(media_id, action, insert_index)


@playlist_router.post(
    "/playlist/move/{playlist_entry_id}",
    summary="Move a Playlist Entry to a different position in the Streamer's active Playlist",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_move_entry(playlist_entry_id: int, from_index: int, to_index: int):
    get_vibin_instance().streamer.playlist_move_entry(
        playlist_entry_id, from_index, to_index
    )


@playlist_router.post(
    "/playlist/clear",
    summary="Clear the Streamer's active Playlist",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_clear():
    get_vibin_instance().streamer.playlist_clear()


@playlist_router.post(
    "/playlist/delete/{playlist_entry_id}",
    summary="Remove a Playlist Entry from the Streamer's active Playlist",
    tags=["Active Playlist"],
    response_class=Response,
)
def playlist_delete_entry(playlist_entry_id: int):
    get_vibin_instance().streamer.playlist_delete_entry(playlist_entry_id)
