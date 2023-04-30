from typing import List

from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinNotFoundError
from vibin.models import Artist
from vibin.server.dependencies import (
    requires_media,
    transform_media_server_urls_if_proxying,
)


def artists_router(
    vibin: Vibin, requires_media, transform_media_server_urls_if_proxying
):
    router = APIRouter()

    @router.get("/artists", summary="", description="", tags=["Artists"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def artists() -> List[Artist]:
        try:
            return vibin.media.artists
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/artists/{artist_id}", summary="", description="", tags=["Artists"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def artist_by_id(artist_id: str):
        try:
            return vibin.media.artist(artist_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return router
