from fastapi import APIRouter, HTTPException

from vibin import VibinError
from vibin.server.dependencies import get_vibin_instance, success

system_router = APIRouter()


@system_router.post(
    "/system/streamer/power_toggle",
    summary="",
    description="",
    tags=["Media System"],
)
def system_power_toggle():
    try:
        get_vibin_instance().streamer.power_toggle()
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/system/streamer/source", summary="", description="", tags=["Media System"]
)
def system_source(source: str):
    try:
        get_vibin_instance().streamer.set_source(source)
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/system/streamer/device_display",
    summary="",
    description="",
    tags=["Media System"],
)
def device_display() -> dict:
    return get_vibin_instance().device_display


@system_router.get(
    "/system/statevars",
    summary="",
    description="",
    tags=["Media System"],
    deprecated=True,
)
def state_vars() -> dict:
    return get_vibin_instance().state_vars
