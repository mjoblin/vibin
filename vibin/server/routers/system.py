from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinError
from vibin.models import CurrentlyPlaying, StreamerDeviceDisplay, SystemState
from vibin.server.dependencies import get_vibin_instance

# -----------------------------------------------------------------------------
# The /system route.
# -----------------------------------------------------------------------------

system_router = APIRouter(prefix="/system")


@system_router.get(
    "", summary="Retrieve the system's state details", tags=["Media System"]
)
def state_vars() -> SystemState:
    return get_vibin_instance().system_state


@system_router.get(
    "/streamer/currently_playing",
    summary="Retrieve details on what is currently playing",
    tags=["Media System"],
)
def state_vars() -> CurrentlyPlaying:
    return get_vibin_instance().currently_playing


@system_router.post(
    "/streamer/power_toggle",
    summary="Toggle the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def system_power_toggle():
    try:
        get_vibin_instance().streamer.power_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/streamer/source",
    summary="Set the Streamer's Media Source",
    tags=["Media System"],
    response_class=Response,
)
def system_source(source: str):
    try:
        get_vibin_instance().streamer.set_source(source)
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/streamer/device_display",
    summary="Retrieve the Streamer's current display",
    tags=["Media System"],
)
def device_display() -> StreamerDeviceDisplay:
    return get_vibin_instance().streamer.device_display


@system_router.get(
    "/statevars",
    summary="Retrieve the system's state variables",
    tags=["Media System"],
    deprecated=True,
)
def state_vars() -> dict[str, Any]:
    return get_vibin_instance().upnp_properties
