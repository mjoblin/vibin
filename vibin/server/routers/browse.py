from pathlib import Path

from fastapi import APIRouter, HTTPException
import xmltodict

from vibin import VibinNotFoundError
from vibin.models import Album, Artist, MediaBrowseSingleLevel, MediaFolder, Track
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /browse route.
# -----------------------------------------------------------------------------

browse_router = APIRouter(prefix="/browse")


@browse_router.get(
    "/path/{media_path:path}",
    summary="Retrieve the contents of a path on the Media Server",
    description="The `media_path` can be nested, e.g. `Album/[All Albums]`.",
    tags=["Browse"],
)
@transform_media_server_urls_if_proxying
@requires_media
def path_contents(
    media_path,
) -> list[MediaFolder | Artist | Album | Track] | Track | None:
    try:
        return get_vibin_instance().media_server.get_path_contents(
            Path(media_path.removeprefix("/"))
        )
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@browse_router.get(
    "/children/{parent_id}",
    summary="Retrieve the children of a Parent ID",
    tags=["Browse"],
)
@transform_media_server_urls_if_proxying
@requires_media
def children(parent_id: str) -> MediaBrowseSingleLevel:
    return get_vibin_instance().media_server.children(parent_id)


@browse_router.get(
    "/metadata/{id}", summary="Retrieve metadata for a Media ID", tags=["Browse"]
)
@transform_media_server_urls_if_proxying
@requires_media
def metadata(id: str):
    return xmltodict.parse(get_vibin_instance().media_server.get_metadata(id))
