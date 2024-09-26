from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import Response

from vibin import VibinError
from vibin.models import (
    AmplifierState,
    CurrentlyPlaying,
    StreamerDeviceDisplay,
    StreamerState,
    SystemState,
    SystemUPnPProperties,
)
from vibin.server.dependencies import get_vibin_instance, requires_amplifier

# -----------------------------------------------------------------------------
# The /system route.
# -----------------------------------------------------------------------------

system_router = APIRouter(prefix="/system")


@system_router.get(
    "", summary="Retrieve the system's state details", tags=["Media System"]
)
def system_state() -> SystemState:
    return get_vibin_instance().system_state


@system_router.post(
    "/power/on",
    summary="Turn on the system's power (all devices)",
    tags=["Media System"],
    response_class=Response,
)
def system_power_on():
    try:
        vibin_instance = get_vibin_instance()

        vibin_instance.streamer.power = "on"

        if vibin_instance.amplifier:
            vibin_instance.amplifier.power = "on"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/power/off",
    summary="Turn off the system's power (all devices)",
    tags=["Media System"],
    response_class=Response,
)
def system_power_off():
    try:
        vibin_instance = get_vibin_instance()

        vibin_instance.streamer.power = "off"

        if vibin_instance.amplifier:
            vibin_instance.amplifier.power = "off"
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


# -----------------------------------------------------------------------------
# Streamer

@system_router.get(
    "/streamer/state",
    summary="Get the Streamer's state",
    tags=["Media System"],
)
def streamer_state() -> StreamerState:
    try:
        return get_vibin_instance().streamer.device_state
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/streamer/power/on",
    summary="Turn on the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def streamer_power_on():
    try:
        get_vibin_instance().streamer.power = "on"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/streamer/power/off",
    summary="Turn off the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def streamer_power_off():
    try:
        get_vibin_instance().streamer.power = "off"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/streamer/power/toggle",
    summary="Toggle the Streamer's power",
    tags=["Media System"],
    response_class=Response,
)
def streamer_power_toggle():
    try:
        get_vibin_instance().streamer.power_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/streamer/currently_playing",
    summary="Retrieve details on what is currently playing",
    tags=["Media System"],
)
def streamer_currently_playing() -> CurrentlyPlaying:
    return get_vibin_instance().currently_playing


@system_router.post(
    "/streamer/audio_source/{source_name}",
    summary="Set the Streamer's Audio Source by name",
    tags=["Media System"],
    response_class=Response,
)
def streamer_set_audio_source(source_name: Any):
    try:
        get_vibin_instance().streamer.set_audio_source(str(source_name))
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.get(
    "/streamer/device_display",
    summary="Retrieve the Streamer's current display",
    tags=["Media System"],
)
def streamer_device_display() -> StreamerDeviceDisplay:
    return get_vibin_instance().streamer.device_display


# -----------------------------------------------------------------------------
# Amplifier

@system_router.get(
    "/amplifier/state",
    summary="Get the Amplifier's state",
    tags=["Media System"],
)
@requires_amplifier(allow_if_off=True)
def amplifier_power() -> AmplifierState:
    try:
        return get_vibin_instance().amplifier.device_state
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/power/on",
    summary="Turn on the Amplifier's power",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["power"], allow_if_off=True)
def amplifier_power_on():
    try:
        get_vibin_instance().amplifier.power = "on"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")

@system_router.post(
    "/amplifier/power/off",
    summary="Turn off the Amplifier's power",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["power"])
def amplifier_power_off():
    try:
        get_vibin_instance().amplifier.power = "off"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/power/toggle",
    summary="Toggle the Amplifier's power",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["power"], allow_if_off=True)
def amplifier_power_toggle():
    try:
        get_vibin_instance().amplifier.power_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")

@system_router.post(
    "/amplifier/volume/up",
    summary="Increase the Amplifier's volume by one unit",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["volume_up_down"])
def amplifier_volume_up():
    try:
        get_vibin_instance().amplifier.volume_up()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/volume/down",
    summary="Decrease the Amplifier's volume by one unit",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["volume_up_down"])
def amplifier_volume_down():
    try:
        get_vibin_instance().amplifier.volume_down()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/volume/{volume}",
    summary="Set the Amplifier's volume",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["volume"])
def amplifier_volume_set(volume: Annotated[float, Path(ge=0.0, le=1.0)]):
    try:
        get_vibin_instance().amplifier.volume = volume
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/mute/on",
    summary="Enable the Amplifier's mute setting",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["mute"])
def amplifier_mute_on():
    try:
        get_vibin_instance().amplifier.mute = "on"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/mute/off",
    summary="Disable the Amplifier's mute setting",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["mute"])
def amplifier_mute_off():
    try:
        get_vibin_instance().amplifier.mute = "off"
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/mute/toggle",
    summary="Toggle the Amplifier's mute setting",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["mute"])
def amplifier_mute_toggle():
    try:
        get_vibin_instance().amplifier.mute_toggle()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@system_router.post(
    "/amplifier/audio_source/{source_name}",
    summary="Set the Amplifier's current Audio Source by name",
    tags=["Media System"],
    response_class=Response,
)
@requires_amplifier(actions=["audio_source"])
def amplifier_set_audio_source(source_name: Any):
    try:
        get_vibin_instance().amplifier.audio_source = str(source_name)
    except VibinError as e:
        if "Invalid source name" in e.args[0]:
            raise HTTPException(status_code=400, detail=f"{e}")
        else:
            raise HTTPException(status_code=500, detail=f"{e}")
