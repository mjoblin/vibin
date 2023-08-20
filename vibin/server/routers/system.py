from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from vibin import VibinError
from vibin.models import (
    CurrentlyPlaying,
    StreamerDeviceDisplay,
    SystemState,
    SystemUPnPProperties,
)
from vibin.server.dependencies import get_vibin_instance

# -----------------------------------------------------------------------------
# The /system route.
# -----------------------------------------------------------------------------

system_router = APIRouter(prefix="/system")


@system_router.get(
    "", summary="Retrieve the system's state details", tags=["Media System"]
)
def system_state() -> SystemState:
    return get_vibin_instance().system_state


@system_router.get(
    "/streamer/currently_playing",
    summary="Retrieve details on what is currently playing",
    tags=["Media System"],
)
def system_streamer_currently_playing() -> CurrentlyPlaying:
    return get_vibin_instance().currently_playing


@system_router.post(
    "/streamer/power_toggle",
    summary="Toggle the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def system_streamer_power_toggle():
    try:
        get_vibin_instance().streamer.power_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/streamer/audio_source",
    summary="Set the Streamer's Audio Source",
    tags=["Media System"],
    response_class=Response,
)
def system_streamer_audio_source(source: str):
    try:
        get_vibin_instance().streamer.set_audio_source(source)
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/streamer/device_display",
    summary="Retrieve the Streamer's current display",
    tags=["Media System"],
)
def system_streamer_device_display() -> StreamerDeviceDisplay:
    return get_vibin_instance().streamer.device_display


@system_router.get(
    "/amplifier/volume",
    summary="Get the Amplifier's volume",
    tags=["Media System"],
)
def system_amplifier_volume() -> float:
    try:
        return get_vibin_instance().amplifier.volume
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/power_toggle",
    summary="Toggle the Amplifier's power",
    tags=["Media System"],
    response_class=Response,
)
def system_amplifier_power_toggle():
    try:
        get_vibin_instance().amplifier.power_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/volume",
    summary="Set the Amplifier's volume",
    tags=["Media System"],
    response_class=Response,
)
def system_amplifier_volume_set(volume: Annotated[float, Query(ge=0.0, le=1.0)]):
    try:
        get_vibin_instance().amplifier.volume = volume
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/mute_toggle",
    summary="Toggle the Amplifier's mute setting",
    tags=["Media System"],
    response_class=Response,
)
def system_amplifier_mute_toggle():
    try:
        get_vibin_instance().amplifier.mute_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/upnp_properties",
    summary="Retrieve the system's UPnP properties",
    description=(
        "**This information is not intended for general client use**, but is "
        + "made available for debugging or as a last-resort fallback. The "
        + "response payload contains low-level UPnP property information "
        + "associated with any UPnP subscriptions which might be active for "
        + "the Streamer and Media Server. Any commonly-useful information for "
        + "clients should be available at other endpoints."
    ),
    tags=["Media System"],
)
def system_upnp_properties() -> SystemUPnPProperties:
    return get_vibin_instance().upnp_properties
