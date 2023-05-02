from fastapi import APIRouter
from fastapi.responses import Response

from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /presets route.
# -----------------------------------------------------------------------------

presets_router = APIRouter()


@presets_router.get("/presets", summary="Retrieve all Preset details", tags=["Presets"])
@transform_media_server_urls_if_proxying
def presets() -> dict:
    return get_vibin_instance().presets


@presets_router.post(
    "/presets/{preset_id}/play",
    summary="Play a Preset",
    tags=["Presets"],
    response_class=Response,
)
def preset_play(preset_id: int):
    get_vibin_instance().streamer.play_preset_id(preset_id)
