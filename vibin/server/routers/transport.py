from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinError
from vibin.models import TransportPlayheadPositionPayload, TransportState
from vibin.server.dependencies import get_vibin_instance
from vibin.types import SeekTarget

# -----------------------------------------------------------------------------
# The /transport route.
# -----------------------------------------------------------------------------

transport_router = APIRouter(prefix="/transport")


@transport_router.get(
    "",
    summary="Retrieve the current Transport details",
    tags=["Transport"],
)
def transport_state() -> TransportState:
    return get_vibin_instance().streamer.transport_state


@transport_router.post("/play", summary="Play the Transport", tags=["Transport"])
def transport_play() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.play()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post(
    "/toggle_playback", summary="Toggle the Transport's play state", tags=["Transport"]
)
def transport_toggle_playback() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.toggle_playback()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post("/pause", summary="Pause the Transport", tags=["Transport"])
def transport_pause() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.pause()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post("/stop", summary="Stop the Transport", tags=["Transport"])
def transport_stop() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.stop()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post("/next", summary="Next Playlist Entry", tags=["Transport"])
def transport_next() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.next_track()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post(
    "/previous", summary="Previous Playlist Entry", tags=["Transport"]
)
def transport_previous() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.previous_track()
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post("/repeat", summary="Toggle repeat", tags=["Transport"])
def transport_repeat() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.repeat("toggle")
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


@transport_router.post("/shuffle", summary="Toggle shuffle", tags=["Transport"])
def transport_shuffle() -> TransportState:
    vibin = get_vibin_instance()

    try:
        vibin.streamer.shuffle("toggle")
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return vibin.streamer.transport_state


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
        get_vibin_instance().streamer.seek(target)
    except VibinError as e:
        raise HTTPException(status_code=500, detail=str(e))


@transport_router.get(
    "/position",
    summary="Retrieve the current Playhead position (in whole seconds)",
    tags=["Transport"],
)
def transport_position() -> TransportPlayheadPositionPayload:
    return TransportPlayheadPositionPayload(
        position=get_vibin_instance().streamer.transport_position
    )


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
