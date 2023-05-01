from fastapi import APIRouter, HTTPException

from vibin import VibinError
from vibin.server.dependencies import get_vibin_instance, success
from vibin.streamers import SeekTarget

transport_router = APIRouter()


@transport_router.post(
    "/transport/pause", summary="", description="", tags=["Transport"]
)
def transport_pause():
    try:
        get_vibin_instance().pause()
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@transport_router.post(
    "/transport/play", summary="", description="", tags=["Transport"]
)
def transport_play():
    try:
        get_vibin_instance().play()
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@transport_router.post(
    "/transport/next", summary="", description="", tags=["Transport"]
)
def transport_next():
    try:
        get_vibin_instance().next_track()
        return success
    except VibinError as e:
        raise HTTPException(status_code=500, detail=f"{e}")


@transport_router.post(
    "/transport/previous", summary="", description="", tags=["Transport"]
)
def transport_previous():
    get_vibin_instance().previous_track()


# TODO: Consider whether repeat and shuffle should be toggles or not.
@transport_router.post(
    "/transport/repeat", summary="", description="", tags=["Transport"]
)
def transport_repeat():
    get_vibin_instance().repeat("toggle")


@transport_router.post(
    "/transport/shuffle", summary="", description="", tags=["Transport"]
)
def transport_shuffle():
    get_vibin_instance().shuffle("toggle")


@transport_router.post(
    "/transport/seek", summary="", description="", tags=["Transport"]
)
def transport_seek(target: SeekTarget):
    get_vibin_instance().seek(target)


@transport_router.get(
    "/transport/position", summary="", description="", tags=["Transport"]
)
def transport_position():
    return {"position": get_vibin_instance().transport_position()}


@transport_router.post(
    "/transport/play/{media_id}", summary="", description="", tags=["Transport"]
)
def transport_play_media_id(media_id: str):
    get_vibin_instance().play_id(media_id)


@transport_router.get(
    "/transport/active_controls", summary="", description="", tags=["Transport"]
)
def transport_active_controls():
    return {"active_controls": get_vibin_instance().transport_active_controls()}


@transport_router.get(
    "/transport/play_state", summary="", description="", tags=["Transport"]
)
def transport_play_state():
    return get_vibin_instance().play_state
