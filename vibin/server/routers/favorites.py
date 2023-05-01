from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinNotFoundError
from vibin.models import Favorite
from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

favorites_router = APIRouter()


@favorites_router.get("/favorites", summary="", description="", tags=["Favorites"])
@transform_media_server_urls_if_proxying
def favorites():
    return {
        "favorites": get_vibin_instance().favorites(),
    }


@favorites_router.get(
    "/favorites/albums", summary="", description="", tags=["Favorites"]
)
@transform_media_server_urls_if_proxying
def favorites_albums():
    favorites = get_vibin_instance().favorites(requested_types=["album"])

    return {
        "favorites": favorites,
    }


@favorites_router.get(
    "/favorites/tracks", summary="", description="", tags=["Favorites"]
)
@transform_media_server_urls_if_proxying
def favorites_tracks():
    favorites = get_vibin_instance().favorites(requested_types=["track"])

    return {
        "favorites": favorites,
    }


@favorites_router.post("/favorites", summary="", description="", tags=["Favorites"])
def favorites_create(favorite: Favorite):
    try:
        return get_vibin_instance().store_favorite(favorite.type, favorite.media_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@favorites_router.delete(
    "/favorites/{media_id}", summary="", description="", tags=["Favorites"]
)
def favorites_delete(media_id):
    get_vibin_instance().delete_favorite(media_id)
