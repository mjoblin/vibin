from fastapi import APIRouter, HTTPException

from fastapi.responses import Response

from vibin import VibinInputError
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
    "/{preset_id}", summary="Retrieve individual Preset details", tags=["Presets"]
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


@presets_router.put(
    "/{preset_id}",
    summary="Add a Preset",
    tags=["Presets"],
    response_class=Response,
)
def preset_add(preset_id: int, media_id: str):
    """Add a Track or Album to a Preset slot."""
    try:
        get_vibin_instance().streamer.preset_add(preset_id, media_id)
    except VibinInputError as e:
        raise HTTPException(status_code=400, detail=str(e))


@presets_router.delete(
    "/{preset_id}",
    summary="Delete a Preset",
    tags=["Presets"],
    response_class=Response,
)
def preset_delete(preset_id: int):
    try:
        get_vibin_instance().streamer.preset_delete(preset_id)
    except VibinInputError as e:
        raise HTTPException(status_code=400, detail=str(e))


@presets_router.post(
    "/{preset_id}/move/{to_id}",
    summary="Move a Preset (overwrites destination)",
    tags=["Presets"],
    response_class=Response,
)
def preset_move(preset_id: int, to_id: int):
    try:
        get_vibin_instance().streamer.preset_move(preset_id, to_id)
    except VibinInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
