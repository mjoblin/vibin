import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinInputError
from vibin.models import VibinStatus, VibinSettings
from vibin.server.dependencies import get_vibin_instance, requires_media, server_status
from vibin.server.routers.websocket_server import ws_connection_manager
from vibin.types import DatabaseName


# -----------------------------------------------------------------------------
# The /vibin route.
# -----------------------------------------------------------------------------

vibin_router = APIRouter(prefix="/vibin")


@vibin_router.get(
    "", summary="Retrieve the current Vibin server status", tags=["Vibin Server"]
)
def vibin_status() -> VibinStatus:
    vibin = get_vibin_instance()

    return server_status(
        websocket_clients=ws_connection_manager.client_details(),
        lyrics_enabled=vibin.lyrics_manager.is_enabled,
        waveforms_enabled=vibin.waveform_manager.is_enabled,
    )


@vibin_router.post(
    "/clear_media_caches",
    summary="Clear media caches",
    description=(
        "Clears the caches of Tracks, Albums, Artists, etc. To be used when "
        + "the UPnP Media Server has been updated with (for example) new "
        + "Albums, updated metadata, etc."
    ),
    tags=["Vibin Server"],
    response_class=Response,
)
@requires_media
def vibin_clear_media_caches() -> None:
    get_vibin_instance().media_server.clear_caches()


@vibin_router.get(
    "/settings",
    summary="Retrieve the current Vibin server settings",
    tags=["Vibin Server"],
)
def vibin_settings() -> VibinSettings:
    return get_vibin_instance().settings


@vibin_router.put(
    "/settings", summary="Update the Vibin server settings", tags=["Vibin Server"]
)
@requires_media
def vibin_update_settings(settings: VibinSettings) -> VibinSettings:
    get_vibin_instance().settings = settings

    return get_vibin_instance().settings


@vibin_router.get(
    "/db/{database_name}",
    summary="Retrieve the contents of a system database as JSON",
    tags=["Vibin Server"],
)
def db_get(database_name: DatabaseName) -> dict[str, Any]:
    try:
        return get_vibin_instance().db_get(database_name)
    except VibinInputError as e:
        raise HTTPException(status_code=400, detail=f"Cannot retrieve database: {e}")


@vibin_router.put(
    "/db/{database_name}",
    summary="Replace the contents of a system database with the provided JSON",
    tags=["Vibin Server"],
)
def db_set(database_name: DatabaseName, data: dict):
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

    try:
        return get_vibin_instance().db_set(database_name, data)
    except VibinInputError as e:
        raise HTTPException(status_code=400, detail=f"Cannot replace database: {e}")
