from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinError
from vibin.models import (
    TransportActiveControls,
    TransportPlayheadPosition,
    TransportPlayState,
    TransportState,
)
from vibin.server.dependencies import get_vibin_instance
from vibin.streamers import SeekTarget

# -----------------------------------------------------------------------------
# The /transport route.
# -----------------------------------------------------------------------------

transport_router = APIRouter(prefix="/transport")


@transport_router.get(
    "",
    summary="Retrieve the current Transport details",
    tags=["Transport"],
)
def transport_active_controls() -> TransportState:
    return get_vibin_instance().transport_state


@transport_router.post(
    "/pause",
    summary="Pause the Transport",
    tags=["Transport"],
    response_class=Response,
)
def transport_pause():
    try:
        get_vibin_instance().pause()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.post(
    "/play",
    summary="Play the Transport",
    tags=["Transport"],
    response_class=Response,
)
def transport_play():
    try:
        get_vibin_instance().play()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.post(
    "/next",
    summary="Next Playlist Entry",
    tags=["Transport"],
    response_class=Response,
)
def transport_next():
    try:
        get_vibin_instance().next_track()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.post(
    "/previous",
    summary="Previous Playlist Entry",
    tags=["Transport"],
    response_class=Response,
)
def transport_previous():
    try:
        get_vibin_instance().previous_track()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


# TODO: Consider whether repeat and shuffle should be toggles or not.
@transport_router.post(
    "/repeat",
    summary="Toggle repeat",
    tags=["Transport"],
    response_class=Response,
)
def transport_repeat():
    try:
        get_vibin_instance().repeat("toggle")
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.post(
    "/shuffle",
    summary="Toggle shuffle",
    tags=["Transport"],
    response_class=Response,
)
def transport_shuffle():
    try:
        get_vibin_instance().shuffle("toggle")
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.post(
    "/seek",
    summary="Seek into the current Playlist Entry",
    description=(
        "`target` can be a float, int, or string. Floats are interpreted as a normalized 0-1 "
        + "duration into the playlist entry (e.g. `0.5` is 50% or half way). "
        + "Ints are interpreted as a number of seconds into the playlist entry (e.g. `20` is 20 "
        + 'seconds into the playlist entry). Strings should be of the format `"h:mm:ss"` (e.g. '
        + '`"0:01:30"` is 1min 30secs into the playlist entry).'
    ),
    tags=["Transport"],
    response_class=Response,
)
def transport_seek(target: SeekTarget):
    try:
        get_vibin_instance().seek(target)
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.get(
    "/position",
    summary="Retrieve the current Playhead position (in whole seconds)",
    tags=["Transport"],
)
def transport_position() -> TransportPlayheadPosition:
    return TransportPlayheadPosition(position=get_vibin_instance().transport_position())


@transport_router.post(
    "/play/{media_id}",
    summary="Play media by Media ID",
    tags=["Transport"],
    response_class=Response,
)
def transport_play_media_id(media_id: str):
    try:
        get_vibin_instance().play_id(media_id)
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.get(
    "/active_controls",
    summary="Retrieve the list of currently-valid Transport controls",
    tags=["Transport"],
    deprecated=True,
)
def transport_active_controls() -> TransportActiveControls:
    return TransportActiveControls(
        active_controls=get_vibin_instance().transport_active_controls()
    )


@transport_router.get(
    "/play_state",
    summary="Retrieve the current play state",
    tags=["Transport"],
    deprecated=True,
)
def transport_play_state() -> TransportPlayState:
    return get_vibin_instance().play_state
