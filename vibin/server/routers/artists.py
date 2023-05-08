from fastapi import APIRouter, HTTPException

from vibin import VibinNotFoundError
from vibin.models import Artist
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /artists route.
# -----------------------------------------------------------------------------

artists_router = APIRouter(prefix="/artists")


@artists_router.get("", summary="Retrieve all Artist details", tags=["Artists"])
@transform_media_server_urls_if_proxying
@requires_media
def artists() -> list[Artist]:
    try:
        return get_vibin_instance().media.artists
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@artists_router.get(
    "/{artist_id}", summary="Retrieve details on a single Artist", tags=["Artists"]
)
@transform_media_server_urls_if_proxying
@requires_media
def artist_by_id(artist_id: str) -> Artist:
    try:
        return get_vibin_instance().media.artist(artist_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
