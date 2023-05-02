from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
import xmltodict

from vibin import VibinNotFoundError
from vibin.models import Track
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /browse route.
# -----------------------------------------------------------------------------

browse_router = APIRouter()


@browse_router.get(
    "/browse/path/{media_path:path}",
    summary="Retrieve the contents of a path on the Media Server",
    description="The `media_path` can be nested, e.g. `Albums/[All Albums]`.",
    tags=["Browse"],
)
@transform_media_server_urls_if_proxying
@requires_media
def path_contents(media_path) -> List | Track:
    try:
        return get_vibin_instance().media.get_path_contents(Path(media_path))
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@browse_router.get(
    "/browse/children/{parent_id}",
    summary="Retrieve the children of the given Parent ID on the Media Server",
    tags=["Browse"],
)
@transform_media_server_urls_if_proxying
@requires_media
def browse(parent_id: str):
    return get_vibin_instance().browse_media(parent_id)


@browse_router.get(
    "/browse/metadata/{id}",
    summary="Retrieve the Media Server's metadata for the given Media ID",
    tags=["Browse"],
)
@transform_media_server_urls_if_proxying
@requires_media
def browse(id: str):
    return xmltodict.parse(get_vibin_instance().media.get_metadata(id))
