import json

from fastapi import APIRouter, HTTPException

from vibin import Vibin
from vibin.models import ServerStatus, VibinSettings
from vibin.server.dependencies import requires_media


def vibin_router(vibin: Vibin, requires_media, server_status):
    router = APIRouter()

    @router.get(
        "/vibin/status",
        summary="Vibin server status",
        description="Returns the current Vibin server status information.",
        tags=["Vibin Server"],
    )
    def vibin_status() -> ServerStatus:
        return server_status()

    @router.post(
        "/vibin/clear_media_caches",
        summary="Clear media caches",
        description="Clears the caches of Tracks, Albums, Artists, etc. To be used when the UPnP Media Server has been updated with (for example) new Albums, updated metadata, etc.",
        tags=["Vibin Server"],
    )
    @requires_media
    def vibin_clear_media_caches():
        return vibin.media.clear_caches()

    @router.get("/vibin/settings", summary="", description="", tags=["Vibin Server"])
    def vibin_settings():
        return vibin.settings

    @router.put("/vibin/settings", summary="", description="", tags=["Vibin Server"])
    @requires_media
    def vibin_update_settings(settings: VibinSettings):
        vibin.settings = settings

        return vibin.settings

    @router.get("/vibin/db", summary="", description="", tags=["Vibin Server"])
    def db_get():
        return vibin.db_get()

    @router.put("/vibin/db", summary="", description="", tags=["Vibin Server"])
    def db_set(data: dict):
        # TODO: This takes a user-provided chunk of data and writes it to disk.
        #   This could be exploited for much harm if used with ill intent.
        try:
            json.dumps(data)
        except (json.decoder.JSONDecodeError, TypeError) as e:
            # TODO: This could do more validation to ensure the provided data
            #   is TinyDB-compliant.
            raise HTTPException(
                status_code=400,
                detail=f"Provided payload is not valid JSON: {e}",
            )

        return vibin.db_set(data)

    return router
