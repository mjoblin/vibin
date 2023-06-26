from fastapi import APIRouter, HTTPException

from vibin import VibinNotFoundError
from vibin.models import Album, Track
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /albums route.
# -----------------------------------------------------------------------------

albums_router = APIRouter(prefix="/albums")


@albums_router.get("", summary="Retrieve all Album details", tags=["Albums"])
@transform_media_server_urls_if_proxying
@requires_media
def albums() -> list[Album]:
    try:
        return get_vibin_instance().media_server.albums
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get("/new", summary="Retrieve new Album details", tags=["Albums"])
@transform_media_server_urls_if_proxying
@requires_media
def albums_new() -> list[Album]:
    try:
        return get_vibin_instance().media_server.new_albums
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get(
    "/{album_id}", summary="Retrieve details on a single Album", tags=["Albums"]
)
@transform_media_server_urls_if_proxying
@requires_media
def album_by_id(album_id: str) -> Album:
    try:
        return get_vibin_instance().media_server.album(album_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get(
    "/{album_id}/tracks",
    summary="Retrieve all Track details for an Album",
    tags=["Albums"],
)
@transform_media_server_urls_if_proxying
@requires_media
def album_tracks(album_id: str) -> list[Track]:
    return get_vibin_instance().media_server.album_tracks(album_id)


@albums_router.get(
    "/{album_id}/links", summary="Retrieve all links for an Album", tags=["Albums"]
)
@requires_media
def album_links(album_id: str, all_types: bool = False):
    return get_vibin_instance().links_manager.media_links(album_id, all_types)
