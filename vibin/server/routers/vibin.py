import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from vibin.models import ServerStatus, VibinSettings
from vibin.server.dependencies import get_vibin_instance, requires_media, server_status
from vibin.server.routers.websocket_server import ws_connection_manager


# -----------------------------------------------------------------------------
# The /vibin route.
# -----------------------------------------------------------------------------

# Models


class VibinServerSummary(BaseModel):
    summary: str


# Endpoints

vibin_router = APIRouter()


@vibin_router.get(
    "/vibin/summary",
    summary="Retrieve the current Vibin server summary",
    description=(
        "The server summary is a simple string describing some high-level "
        + "information about the server."
    ),
    tags=["Vibin Server"],
)
def vibin_summary() -> VibinServerSummary:
    return VibinServerSummary(summary=str(get_vibin_instance()))


@vibin_router.get(
    "/vibin/status",
    summary="Retrieve the current Vibin server status",
    tags=["Vibin Server"],
)
def vibin_status() -> ServerStatus:
    return server_status(
        websocket_clients=ws_connection_manager.client_details()
    )


@vibin_router.post(
    "/vibin/clear_media_caches",
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
    get_vibin_instance().media.clear_caches()


@vibin_router.get(
    "/vibin/settings",
    summary="Retrieve the current Vibin server settings",
    tags=["Vibin Server"],
)
def vibin_settings() -> VibinSettings:
    return get_vibin_instance().settings


@vibin_router.put(
    "/vibin/settings",
    summary="Update the Vibin server settings",
    tags=["Vibin Server"],
)
@requires_media
def vibin_update_settings(settings: VibinSettings) -> VibinSettings:
    get_vibin_instance().settings = settings

    return get_vibin_instance().settings


@vibin_router.get(
    "/vibin/db",
    summary="Retrieve the Database contents (as JSON)",
    tags=["Vibin Server"],
)
def db_get() -> dict[str, Any]:
    return get_vibin_instance().db_get()


@vibin_router.put(
    "/vibin/db",
    summary="Replace the Database with the provided JSON",
    tags=["Vibin Server"],
)
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

    return get_vibin_instance().db_set(data)
