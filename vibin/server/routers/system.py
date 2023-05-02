from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinError
from vibin.server.dependencies import get_vibin_instance, success

# -----------------------------------------------------------------------------
# The /system route.
# -----------------------------------------------------------------------------

system_router = APIRouter()


@system_router.post(
    "/system/streamer/power_toggle",
    summary="Toggle the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def system_power_toggle():
    try:
        get_vibin_instance().streamer.power_toggle()
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/system/streamer/source",
    summary="Set the Streamer's Media Source",
    tags=["Media System"],
    response_class=Response,
)
def system_source(source: str):
    try:
        get_vibin_instance().streamer.set_source(source)
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/system/streamer/device_display",
    summary="Retrieve the Streamer's current display",
    tags=["Media System"],
)
def device_display() -> dict:
    return get_vibin_instance().device_display


@system_router.get(
    "/system/statevars",
    summary="Retrieve the system's state variables",
    tags=["Media System"],
    deprecated=True,
)
def state_vars() -> dict[str, Any]:
    return get_vibin_instance().state_vars
