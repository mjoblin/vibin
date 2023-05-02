from typing import List

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

albums_router = APIRouter()


@albums_router.get("/albums", summary="Retrieve all Album details", tags=["Albums"])
@transform_media_server_urls_if_proxying
@requires_media
def albums() -> List[Album]:
    try:
        return get_vibin_instance().media.albums
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get("/albums/new", summary="Retrieve new Album details", tags=["Albums"])
@transform_media_server_urls_if_proxying
@requires_media
def albums_new() -> List[Album]:
    try:
        return get_vibin_instance().media.new_albums
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get(
    "/albums/{album_id}",
    summary="Retrieve details on a single Album",
    tags=["Albums"],
)
@transform_media_server_urls_if_proxying
@requires_media
def album_by_id(album_id: str) -> Album:
    try:
        return get_vibin_instance().media.album(album_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@albums_router.get(
    "/albums/{album_id}/tracks",
    summary="Retrieve all Track details for an Album",
    tags=["Albums"],
)
@transform_media_server_urls_if_proxying
@requires_media
def album_tracks(album_id: str) -> List[Track]:
    return get_vibin_instance().media.album_tracks(album_id)


@albums_router.get(
    "/albums/{album_id}/links",
    summary="Retrieve all links for an Album",
    tags=["Albums"],
)
@requires_media
def album_links(album_id: str, all_types: bool = False):
    return get_vibin_instance().media_links(album_id, all_types)
