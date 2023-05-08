from fastapi import APIRouter, HTTPException

from vibin import VibinNotFoundError
from vibin.models import Favorite, Favorites
from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /favorites route.
# -----------------------------------------------------------------------------

favorites_router = APIRouter(prefix="/favorites")


@favorites_router.get("", summary="Retrieve all Favorites", tags=["Favorites"])
@transform_media_server_urls_if_proxying
def favorites() -> Favorites:
    return Favorites(favorites=get_vibin_instance().favorites())


@favorites_router.get(
    "/albums", summary="Retrieve all Album Favorites", tags=["Favorites"]
)
@transform_media_server_urls_if_proxying
def favorites_albums():
    favorite_albums = get_vibin_instance().favorites(requested_types=["album"])

    return Favorites(favorites=favorite_albums)


@favorites_router.get(
    "/tracks", summary="Retrieve all Track Favorites", tags=["Favorites"]
)
@transform_media_server_urls_if_proxying
def favorites_tracks():
    favorite_tracks = get_vibin_instance().favorites(requested_types=["track"])

    return Favorites(favorites=favorite_tracks)


@favorites_router.post("", summary="Favorite an Album or Track", tags=["Favorites"])
def favorites_create(favorite: Favorite):
    try:
        return get_vibin_instance().store_favorite(favorite.type, favorite.media_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@favorites_router.delete(
    "/{media_id}", summary="Unfavorite an Album or Track", tags=["Favorites"]
)
def favorites_delete(media_id):
    get_vibin_instance().delete_favorite(media_id)
