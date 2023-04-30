from fastapi import APIRouter

from vibin import Vibin
from vibin.server.dependencies import transform_media_server_urls_if_proxying


def presets_router(vibin: Vibin, transform_media_server_urls_if_proxying):
    router = APIRouter()

    @router.get("/presets", summary="", description="", tags=["Presets"])
    @transform_media_server_urls_if_proxying
    def presets() -> dict:
        return vibin.presets

    @router.post(
        "/presets/{preset_id}/play", summary="", description="", tags=["Presets"]
    )
    def preset_play(preset_id: int):
        return vibin.streamer.play_preset_id(preset_id)

    return router
