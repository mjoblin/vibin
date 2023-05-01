from typing import List

from fastapi import APIRouter, HTTPException

from vibin import VibinNotFoundError
from vibin.models import Artist
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

artists_router = APIRouter()


@artists_router.get("/artists", summary="", description="", tags=["Artists"])
@transform_media_server_urls_if_proxying
@requires_media
def artists() -> List[Artist]:
    try:
        return get_vibin_instance().media.artists
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@artists_router.get(
    "/artists/{artist_id}", summary="", description="", tags=["Artists"]
)
@transform_media_server_urls_if_proxying
@requires_media
def artist_by_id(artist_id: str):
    try:
        return get_vibin_instance().media.artist(artist_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
