from typing import List

from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinNotFoundError
from vibin.models import Album, Track


def albums_router(
    vibin: Vibin, requires_media, transform_media_server_urls_if_proxying
):
    router = APIRouter()

    @router.get("/albums", summary="", description="", tags=["Albums"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def albums() -> List[Album]:
        try:
            return vibin.media.albums
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/albums/new", summary="", description="", tags=["Albums"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def albums_new() -> List[Album]:
        try:
            return vibin.media.new_albums
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/albums/{album_id}", summary="", description="", tags=["Albums"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def album_by_id(album_id: str) -> Album:
        try:
            return vibin.media.album(album_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get(
        "/albums/{album_id}/tracks", summary="", description="", tags=["Albums"]
    )
    @transform_media_server_urls_if_proxying
    @requires_media
    def album_tracks(album_id: str) -> List[Track]:
        return vibin.media.album_tracks(album_id)

    @router.get("/albums/{album_id}/links", summary="", description="", tags=["Albums"])
    @requires_media
    def album_links(album_id: str, all_types: bool = False):
        return vibin.media_links(album_id, all_types)

    return router
