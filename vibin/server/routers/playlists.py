from typing import Optional

from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinDeviceError, VibinNotFoundError
from vibin.models import StoredPlaylist


def playlists_router(vibin: Vibin):
    router = APIRouter()

    @router.get("/playlists", summary="", description="", tags=["Stored Playlists"])
    def playlists() -> list[StoredPlaylist]:
        return vibin.playlists()

    @router.get(
        "/playlists/{playlist_id}",
        summary="",
        description="",
        tags=["Stored Playlists"],
    )
    def playlists_id(playlist_id: str) -> StoredPlaylist:
        playlist = vibin.get_playlist(playlist_id)

        if playlist is None:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

        return playlist

    @router.put(
        "/playlists/{playlist_id}",
        summary="",
        description="",
        tags=["Stored Playlists"],
    )
    def playlists_id_update(
        playlist_id: str, name: Optional[str] = None
    ) -> StoredPlaylist:
        metadata = {"name": name} if name else None

        try:
            return vibin.update_playlist_metadata(
                playlist_id=playlist_id, metadata=metadata
            )
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

    @router.delete(
        "/playlists/{playlist_id}",
        status_code=204,
        summary="",
        description="",
        tags=["Stored Playlists"],
    )
    def playlists_id_delete(playlist_id: str):
        try:
            vibin.delete_playlist(playlist_id=playlist_id)
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

    @router.post(
        "/playlists/{playlist_id}/make_current",
        summary="",
        description="",
        tags=["Stored Playlists"],
    )
    def playlists_id_make_current(playlist_id: str) -> StoredPlaylist:
        # TODO: Is it possible to configure FastAPI to always treat
        #   VibinNotFoundError as a 404 and VibinDeviceError as a 503?
        try:
            return vibin.set_current_playlist(playlist_id)
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )
        except VibinDeviceError as e:
            raise HTTPException(status_code=503, detail=f"Downstream device error: {e}")

    @router.post(
        "/playlists/current/store",
        summary="",
        description="",
        tags=["Stored Playlists"],
    )
    def playlists_current_store(
        name: Optional[str] = None, replace: Optional[bool] = True
    ):
        metadata = {"name": name} if name else None

        return vibin.store_current_playlist(metadata=metadata, replace=replace)

    return router
