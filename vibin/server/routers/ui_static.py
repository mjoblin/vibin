from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from vibin.server.dependencies import get_ui_static_root

# -----------------------------------------------------------------------------
# The /ui route for serving the UI's static files.
# -----------------------------------------------------------------------------

ui_static_router = APIRouter()


@ui_static_router.get("/ui", include_in_schema=False)
def serve_ui_root_index_html():
    ui_static_root = get_ui_static_root()

    if ui_static_root is None:
        raise HTTPException(
            status_code=404,
            detail="Web UI unavailable; see 'vibin serve --vibinui'",
        )

    return FileResponse(path=Path(ui_static_root, "index.html"))


@ui_static_router.get("/ui/{resource}", include_in_schema=False)
def serve_ui_index_html(resource: str):
    ui_static_root = get_ui_static_root()

    if ui_static_root is None:
        raise HTTPException(
            status_code=404,
            detail="Web UI unavailable; see 'vibin serve --vibinui'",
        )

    if resource in [
        "albums",
        "artists",
        "current",
        "favorites",
        "playlist",
        "presets",
        "status",
        "tracks",
    ]:
        return FileResponse(path=Path(ui_static_root, "index.html"))

    return FileResponse(path=Path(ui_static_root, resource))
