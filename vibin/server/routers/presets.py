from fastapi import APIRouter, HTTPException

from fastapi.responses import Response

from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)
from vibin.models import Preset, Presets

# -----------------------------------------------------------------------------
# The /presets route.
# -----------------------------------------------------------------------------

presets_router = APIRouter(prefix="/presets")


@presets_router.get("", summary="Retrieve all Preset details", tags=["Presets"])
@transform_media_server_urls_if_proxying
def presets() -> Presets:
    return get_vibin_instance().streamer.presets


@presets_router.get(
    "/{preset_id}", summary="Retrieve all Preset details", tags=["Presets"]
)
@transform_media_server_urls_if_proxying
def preset_by_id(preset_id: int) -> Preset:
    preset = next(
        (
            preset
            for preset in get_vibin_instance().streamer.presets.presets
            if preset.id == preset_id
        ),
        None,
    )

    if preset is None:
        raise HTTPException(
            status_code=404, detail=str(f"Preset with id {preset_id} not found")
        )

    return preset


@presets_router.post(
    "/{preset_id}/play",
    summary="Play a Preset",
    tags=["Presets"],
    response_class=Response,
)
def preset_play(preset_id: int):
    get_vibin_instance().streamer.play_preset_id(preset_id)
