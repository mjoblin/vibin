from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinNotFoundError
from vibin.models import Favorite
from vibin.server.dependencies import transform_media_server_urls_if_proxying


def favorites_router(vibin: Vibin, transform_media_server_urls_if_proxying):
    router = APIRouter()

    @router.get("/favorites", summary="", description="", tags=["Favorites"])
    @transform_media_server_urls_if_proxying
    def favorites():
        return {
            "favorites": vibin.favorites(),
        }

    @router.get("/favorites/albums", summary="", description="", tags=["Favorites"])
    @transform_media_server_urls_if_proxying
    def favorites_albums():
        favorites = vibin.favorites(requested_types=["album"])

        return {
            "favorites": favorites,
        }

    @router.get("/favorites/tracks", summary="", description="", tags=["Favorites"])
    @transform_media_server_urls_if_proxying
    def favorites_tracks():
        favorites = vibin.favorites(requested_types=["track"])

        return {
            "favorites": favorites,
        }

    @router.post("/favorites", summary="", description="", tags=["Favorites"])
    def favorites_create(favorite: Favorite):
        try:
            return vibin.store_favorite(favorite.type, favorite.media_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.delete(
        "/favorites/{media_id}", summary="", description="", tags=["Favorites"]
    )
    def favorites_delete(media_id):
        vibin.delete_favorite(media_id)

    return router
