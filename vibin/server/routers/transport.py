from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinError
from vibin.streamers import SeekTarget

from vibin.server.dependencies import success


def transport_router(vibin: Vibin):
    router = APIRouter()

    @router.post("/transport/pause", summary="", description="", tags=["Transport"])
    def transport_pause():
        try:
            vibin.pause()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @router.post("/transport/play", summary="", description="", tags=["Transport"])
    def transport_play():
        try:
            vibin.play()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @router.post("/transport/next", summary="", description="", tags=["Transport"])
    def transport_next():
        try:
            vibin.next_track()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @router.post("/transport/previous", summary="", description="", tags=["Transport"])
    def transport_previous():
        vibin.previous_track()

    # TODO: Consider whether repeat and shuffle should be toggles or not.
    @router.post("/transport/repeat", summary="", description="", tags=["Transport"])
    def transport_repeat():
        vibin.repeat("toggle")

    @router.post("/transport/shuffle", summary="", description="", tags=["Transport"])
    def transport_shuffle():
        vibin.shuffle("toggle")

    @router.post("/transport/seek", summary="", description="", tags=["Transport"])
    def transport_seek(target: SeekTarget):
        vibin.seek(target)

    @router.get("/transport/position", summary="", description="", tags=["Transport"])
    def transport_position():
        return {"position": vibin.transport_position()}

    @router.post(
        "/transport/play/{media_id}", summary="", description="", tags=["Transport"]
    )
    def transport_play_media_id(media_id: str):
        vibin.play_id(media_id)

    @router.get(
        "/transport/active_controls", summary="", description="", tags=["Transport"]
    )
    def transport_active_controls():
        return {"active_controls": vibin.transport_active_controls()}

    @router.get("/transport/play_state", summary="", description="", tags=["Transport"])
    def transport_play_state():
        return vibin.play_state

    return router
