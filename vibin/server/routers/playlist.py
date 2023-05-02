from typing import Optional

from fastapi import APIRouter, HTTPException

from vibin import Vibin
from vibin.models import PlaylistModifyPayload


def playlist_router(vibin: Vibin, transform_media_server_urls_if_proxying):
    router = APIRouter()

    @router.get("/playlist", summary="", description="", tags=["Active Playlist"])
    @transform_media_server_urls_if_proxying
    def playlist():
        return vibin.streamer.playlist()

    @router.post(
        "/playlist/modify", summary="", description="", tags=["Active Playlist"]
    )
    def playlist_modify_multiple_entries(payload: PlaylistModifyPayload):
        if payload.action != "REPLACE":
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported action: {payload.action}. Supported actions: REPLACE.",
            )

        return vibin.play_ids(payload.media_ids, max_count=payload.max_count)

    @router.post(
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
        return vibin.modify_playlist(media_id, action, insert_index)

    @router.post(
        "/playlist/play/id/{playlist_entry_id}",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_play_id(playlist_entry_id: int):
        return vibin.streamer.play_playlist_id(playlist_entry_id)

    @router.post(
        "/playlist/play/index/{index}",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_play_index(index: int):
        return vibin.streamer.play_playlist_index(index)

    @router.post(
        "/playlist/play/favorites/albums",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_play_favorite_albums(max_count: int = 10):
        return vibin.play_favorite_albums(max_count=max_count)

    @router.post(
        "/playlist/play/favorites/tracks",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_play_favorite_tracks(max_count: int = 100):
        return vibin.play_favorite_tracks(max_count=max_count)

    @router.post(
        "/playlist/clear", summary="", description="", tags=["Active Playlist"]
    )
    def playlist_clear():
        return vibin.streamer.playlist_clear()

    @router.post(
        "/playlist/delete/{playlist_entry_id}",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_delete_entry(playlist_entry_id: int):
        return vibin.streamer.playlist_delete_entry(playlist_entry_id)

    @router.post(
        "/playlist/move/{playlist_entry_id}",
        summary="",
        description="",
        tags=["Active Playlist"],
    )
    def playlist_move_entry(playlist_entry_id: int, from_index: int, to_index: int):
        return vibin.streamer.playlist_move_entry(
            playlist_entry_id, from_index, to_index
        )

    return router
