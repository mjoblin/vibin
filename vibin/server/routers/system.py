from fastapi import APIRouter, HTTPException

from vibin import Vibin, VibinError

from vibin.server.dependencies import success


def system_router(vibin: Vibin):
    router = APIRouter()

    @router.post(
        "/system/streamer/power_toggle",
        summary="",
        description="",
        tags=["Media System"],
    )
    def system_power_toggle():
        try:
            vibin.streamer.power_toggle()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @router.post(
        "/system/streamer/source", summary="", description="", tags=["Media System"]
    )
    def system_source(source: str):
        try:
            vibin.streamer.set_source(source)
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @router.get(
        "/system/streamer/device_display",
        summary="",
        description="",
        tags=["Media System"],
    )
    def device_display() -> dict:
        return vibin.device_display

    @router.get(
        "/system/statevars",
        summary="",
        description="",
        tags=["Media System"],
        deprecated=True,
    )
    def state_vars() -> dict:
        return vibin.state_vars

    return router
