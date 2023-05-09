from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinDeviceError, VibinNotFoundError
from vibin.models import StoredPlaylist
from vibin.server.dependencies import get_vibin_instance

# -----------------------------------------------------------------------------
# The /stored_playlists route.
# -----------------------------------------------------------------------------

stored_playlists_router = APIRouter(prefix="/stored_playlists")


@stored_playlists_router.get(
    "", summary="Retrieve details on all Stored Playlists", tags=["Stored Playlists"]
)
def playlists() -> list[StoredPlaylist]:
    return get_vibin_instance().playlists()


@stored_playlists_router.get(
    "/{playlist_id}",
    summary="Retrieve details on a single Stored Playlist",
    tags=["Stored Playlists"],
)
def playlists_id(playlist_id: str) -> StoredPlaylist:
    playlist = get_vibin_instance().get_playlist(playlist_id)

    if playlist is None:
        raise HTTPException(
            status_code=404, detail=f"Playlist not found: {playlist_id}"
        )

    return playlist


@stored_playlists_router.put(
    "/{playlist_id}", summary="Update a Stored Playlist", tags=["Stored Playlists"]
)
def playlists_id_update(playlist_id: str, name: str | None = None) -> StoredPlaylist:
    metadata = {"name": name} if name else None

    try:
        return get_vibin_instance().update_playlist_metadata(
            playlist_id=playlist_id, metadata=metadata
        )
    except VibinNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Playlist not found: {playlist_id}"
        )


@stored_playlists_router.delete(
    "/{playlist_id}",
    status_code=204,
    summary="Delete a Stored Playlist",
    tags=["Stored Playlists"],
    response_class=Response,
)
def playlists_id_delete(playlist_id: str):
    try:
        get_vibin_instance().delete_playlist(playlist_id=playlist_id)
    except VibinNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Playlist not found: {playlist_id}"
        )


@stored_playlists_router.post(
    "/{playlist_id}/make_current",
    summary="Activate a Stored Playlist",
    description=(
        "Activating a Stored Playlist means replacing the Streamer's active "
        + "Playlist with the contents of the Stored Playlist."
    ),
    tags=["Stored Playlists"],
)
def playlists_id_make_current(playlist_id: str) -> StoredPlaylist:
    # TODO: Is it possible to configure FastAPI to always treat
    #   VibinNotFoundError as a 404 and VibinDeviceError as a 503?
    try:
        return get_vibin_instance().set_active_playlist(playlist_id)
    except VibinNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Playlist not found: {playlist_id}"
        )
    except VibinDeviceError as e:
        raise HTTPException(status_code=503, detail=f"Downstream device error: {e}")


@stored_playlists_router.post(
    "/current/store",
    summary="Store the Streamer's active Playlist as a new Stored Playlist",
    tags=["Stored Playlists"],
)
def playlists_current_store(
    name: str | None = None, replace: bool | None = True
) -> StoredPlaylist:
    metadata = {"name": name} if name else None

    return get_vibin_instance().store_active_playlist(
        metadata=metadata, replace=replace
    )
