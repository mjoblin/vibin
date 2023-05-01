from fastapi import APIRouter

from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

presets_router = APIRouter()


@presets_router.get("/presets", summary="", description="", tags=["Presets"])
@transform_media_server_urls_if_proxying
def presets() -> dict:
    return get_vibin_instance().presets


@presets_router.post(
    "/presets/{preset_id}/play", summary="", description="", tags=["Presets"]
)
def preset_play(preset_id: int):
    return get_vibin_instance().streamer.play_preset_id(preset_id)
