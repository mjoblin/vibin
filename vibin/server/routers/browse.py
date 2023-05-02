from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
import xmltodict

from vibin import Vibin, VibinNotFoundError
from vibin.models import Track


def browse_router(
    vibin: Vibin, requires_media, transform_media_server_urls_if_proxying
):
    router = APIRouter()

    @router.get(
        "/browse/path/{media_path:path}", summary="", description="", tags=["Browse"]
    )
    @transform_media_server_urls_if_proxying
    @requires_media
    def path_contents(media_path) -> List | Track:
        try:
            return vibin.media.get_path_contents(Path(media_path))
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get(
        "/browse/children/{parent_id}", summary="", description="", tags=["Browse"]
    )
    @transform_media_server_urls_if_proxying
    @requires_media
    def browse(parent_id: str):
        return vibin.browse_media(parent_id)

    @router.get("/browse/metadata/{id}", summary="", description="", tags=["Browse"])
    @transform_media_server_urls_if_proxying
    @requires_media
    def browse(id: str):
        return xmltodict.parse(vibin.media.get_metadata(id))

    return router
