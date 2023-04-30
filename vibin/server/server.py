from contextlib import asynccontextmanager
import os
from pathlib import Path
import platform
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import starlette.requests
import uvicorn

from vibin import VibinError
from vibin.constants import VIBIN_PORT
from vibin.models import ServerStatus, WebSocketClientDetails
from vibin.server.routers import (
    albums_router,
    artists_router,
    browse_router,
    favorites_router,
    media_server_proxy_router,
    playlist_router,
    playlists_router,
    presets_router,
    system_router,
    tracks_router,
    transport_router,
    vibin_router,
    websocket_connection_manager,
    websocket_server_router,
)
from vibin.logger import logger
from vibin.utils import get_local_ip
from .dependencies import (
    get_vibin_instance,
    get_media_server_proxy_client,
    get_media_server_proxy_target,
    is_proxy_for_media_server,
    requires_media,
    transform_media_server_urls_if_proxying,
)

UPNP_EVENTS_BASE_ROUTE = "/upnpevents"
HOSTNAME = "192.168.1.30"


def server_start(
    host="0.0.0.0",
    port=VIBIN_PORT,
    streamer=None,
    media=None,
    discovery_timeout=5,
    vibinui=None,
    proxy_media_server=False,
):
    local_ip = get_local_ip() if host == "0.0.0.0" else host

    if vibinui is not None and not os.path.exists(vibinui):
        logger.error(f"Cannot serve Web UI: Path does not exist: {vibinui}")
        return

    # TODO: This could be in a FastAPI on_startup handler.
    try:
        vibin = get_vibin_instance(
            media=media,
            streamer=streamer,
            discovery_timeout=discovery_timeout,
            subscribe_callback_base=f"http://{local_ip}:{port}{UPNP_EVENTS_BASE_ROUTE}",
            proxy_media_server=proxy_media_server,
        )
    except VibinError as e:
        logger.error(f"Vibin server start unsuccessful: {e}")
        return

    # Configure art reverse proxy to media server
    media_server_proxy_client = None

    # TODO: Keep maybe just the logging and error handling here for proxying
    if is_proxy_for_media_server():
        if vibin.media is not None:
            # proxy_target = get_media_server_proxy_target()
            # media_server_proxy_client = httpx.AsyncClient(base_url=proxy_target)
            logger.info(f"Proxying art at /proxy (target: {get_media_server_proxy_target()})")
        else:
            error = "Unable to proxy art; media server not located"
            logger.error(error)
            vibin.shutdown()

            raise VibinError(error)

    @asynccontextmanager
    async def api_lifespan(app: FastAPI):
        # No FastAPI startup tasks.
        yield

        # FastAPI shutdown tasks.
        logger.info("Vibin server shutdown requested")
        vibin.shutdown()

        media_server_proxy_client = get_media_server_proxy_client()

        if media_server_proxy_client is not None:
            logger.info("Shutting down media server proxy")
            await media_server_proxy_client.aclose()

        logger.info("Shutting down WebSocket connection manager")
        websocket_connection_manager.shutdown()

        logger.info("Vibin server successfully shut down")

    tags_metadata = [
        {
            "name": "Vibin Server",
            "description": "Interact with the Vibin Server's top-level capabilities",
        },
        {
            "name": "Media System",
            "description": "Interact with devices in the media system as a whole (Streamer and Media Source)",
        },
        {"name": "Transport", "description": "Interact with the Streamer transport"},
        {"name": "Browse", "description": "Browse media on the Media Server"},
        {"name": "Tracks", "description": "Interact with Tracks"},
        {"name": "Albums", "description": "Interact with Albums"},
        {"name": "Artists", "description": "Interact with Artists"},
        {
            "name": "Active Playlist",
            "description": "Interact with the current active streamer Playlist",
        },
        {"name": "Stored Playlists", "description": "Interact with Stored Playlists"},
        {"name": "Favorites", "description": "Interact with Favorites"},
        {"name": "Presets", "description": "Interact with Presets"},
    ]

    vibin_app = FastAPI(
        title="vibin",
        description="REST API for the vibin backend.",
        # version=__version__,  # TODO: Get version from pyproject.toml
        openapi_tags=tags_metadata,
        lifespan=api_lifespan,
    )

    # -------------------------------------------------------------------------
    # Proxy the Web UI.
    #
    # The following is a pretty hideous way of serving the UI from FastAPI.
    # It sets up a separate mount under /ui/static (which should serve
    # everything under that directory recursively). Then it hardcodes both /ui
    # and /ui/*; where it checks /ui/* against a list of the *UI's internal
    # routes* for which it just serves index.html and lets the UI's router take
    # it from there. This ensures that a UI route can be properly reloaded from
    # the browser (e.g. Cmd-R on /ui/albums).
    #
    # This currently feels very hacky and is likely prone to issues.
    #
    # See "Correct default route usage":
    #   https://github.com/tiangolo/fastapi/discussions/9146

    if vibinui is not None:
        try:
            logger.info(f"Serving Web UI from: {vibinui}")

            vibin_app.mount(
                "/ui/static",
                StaticFiles(directory=Path(vibinui, "static"), html=True),
                name="vibinui",
            )

            logger.info(f"Web UI available at http://{local_ip}:{port}/ui")
        except RuntimeError as e:
            logger.error(f"Cannot serve Web UI: {e}")
    else:
        logger.info(f"Not serving Web UI")

    @vibin_app.router.get("/", include_in_schema=False)
    def redirect_root_to_ui():
        return RedirectResponse("/ui", status_code=303)

    @vibin_app.router.get("/ui", include_in_schema=False)
    def serve_ui_root_index_html():
        if not vibinui:
            raise HTTPException(
                status_code=404,
                detail="Web UI unavailable; see 'vibin serve --vibinui'",
            )

        return FileResponse(path=Path(vibinui, "index.html"))

    @vibin_app.router.get("/ui/{resource}", include_in_schema=False)
    def serve_ui_index_html(resource: str):
        if not vibinui:
            raise HTTPException(
                status_code=404,
                detail="Web UI unavailable; see 'vibin serve --vibinui'",
            )

        if resource in [
            "albums",
            "artists",
            "current",
            "favorites",
            "playlist",
            "presets",
            "status",
            "tracks",
        ]:
            return FileResponse(path=Path(vibinui, "index.html"))

        return FileResponse(path=Path(vibinui, resource))

    # -------------------------------------------------------------------------

    start_time = time.time()
    connected_websockets = {}

    def server_status() -> ServerStatus:
        clients: list[WebSocketClientDetails] = []

        for websocket_info in connected_websockets.values():
            client_ip, client_port = websocket_info["websocket"].client

            clients.append(
                WebSocketClientDetails(
                    id=websocket_info["id"],
                    when_connected=websocket_info["when_connected"],
                    ip=client_ip,
                    port=client_port,
                )
            )

        return ServerStatus(
            start_time=start_time,
            system_node=platform.node(),
            system_platform=platform.platform(),
            system_version=platform.version(),
            clients=clients,
        )

    # -------------------------------------------------------------------------
    # Add the REST API routers.
    # -------------------------------------------------------------------------

    vibin_app.include_router(vibin_router(vibin, requires_media, server_status))
    vibin_app.include_router(system_router(vibin))
    vibin_app.include_router(transport_router(vibin))
    vibin_app.include_router(
        browse_router(vibin, requires_media, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(
        albums_router(vibin, requires_media, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(
        artists_router(vibin, requires_media, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(
        tracks_router(vibin, requires_media, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(
        playlist_router(vibin, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(playlists_router(vibin))
    vibin_app.include_router(
        favorites_router(vibin, transform_media_server_urls_if_proxying)
    )
    vibin_app.include_router(
        presets_router(vibin, transform_media_server_urls_if_proxying)
    )

    # -------------------------------------------------------------------------
    # Add the non-REST-API routers (media server proxy, and WebSocket server).
    # -------------------------------------------------------------------------

    vibin_app.include_router(media_server_proxy_router)
    vibin_app.include_router(websocket_server_router)

    # -------------------------------------------------------------------------

    @vibin_app.api_route(
        UPNP_EVENTS_BASE_ROUTE + "/{service}",
        methods=["NOTIFY"],
        summary="",
        description="",
        tags=[""],
    )
    async def listen(service: str, request: starlette.requests.Request) -> None:
        body = await request.body()
        vibin.upnp_event(service, body.decode("utf-8"))

    # -------------------------------------------------------------------------

    logger.info(f"Starting REST API")
    logger.info(f"API docs: http://{local_ip}:{port}{vibin_app.docs_url}")

    uvicorn.config.LOGGING_CONFIG["formatters"]["default"][
        "fmt"
    ] = "%(asctime)s %(name)s [%(levelname)s] %(message)s"
    uvicorn.config.LOGGING_CONFIG["formatters"]["access"][
        "fmt"
    ] = "%(asctime)s %(name)s [%(levelname)s] %(client_addr)s [%(status_code)s] %(request_line)s"

    uvicorn.run(
        vibin_app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    server_start()
