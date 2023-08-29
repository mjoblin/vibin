from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from vibin import VibinError
from vibin.constants import VIBIN_PORT, VIBIN_VER
from vibin.logger import logger
from vibin.server.dependencies import (
    get_vibin_instance,
    get_media_server_proxy_client,
    UPNP_EVENTS_BASE_ROUTE,
)
from vibin.server.routers import (
    albums_router,
    artists_router,
    browse_router,
    favorites_router,
    media_server_proxy_router,
    playlist_router,
    presets_router,
    stored_playlists_router,
    system_router,
    tracks_router,
    transport_router,
    ui_static_router,
    upnp_events_router,
    vibin_router,
    ws_connection_manager,
    websocket_server_router,
)
from vibin.utils import get_local_ip


def server_start(
    host="0.0.0.0",
    port=VIBIN_PORT,
    streamer=None,
    streamer_type=None,
    media_server=None,
    media_server_type=None,
    amplifier=None,
    amplifier_type=None,
    discovery_timeout=5,
    vibinui=None,
    proxy_media_server=False,
):
    """Start the Vibin server.

    This process includes:

        * Instantiating a Vibin instance to manage talking to the streamer and
          media server. This instance is available to all the routes that need
          it.
        * Create a FastAPI application, add its routers, and start the app.
            * The routers include: REST API routes; a WebSocket server route;
              and a media server proxy route.
        * Expose a /upnpevents/{service} endpoint to receive UPnP events and
          forward them to the Vibin instance for handling.
    """
    local_ip = get_local_ip() if host == "0.0.0.0" else host

    if vibinui is not None and not os.path.exists(vibinui):
        logger.error(f"Cannot serve Web UI: Path does not exist: {vibinui}")
        return

    # Create the Vibin instance. This instance is effectively a singleton and
    # is created before the API is started. This means that the vibin instance
    # will be available to any routers that need it via the 'dependencies'
    # module.
    try:
        vibin = get_vibin_instance(
            streamer=streamer,
            streamer_type=streamer_type,
            media_server=media_server,
            media_server_type=media_server_type,
            amplifier=amplifier,
            amplifier_type=amplifier_type,
            discovery_timeout=discovery_timeout,
            upnp_subscription_callback_base=f"http://{local_ip}:{port}{UPNP_EVENTS_BASE_ROUTE}",
            proxy_media_server=proxy_media_server,
            ui_static_root=vibinui,
        )
    except VibinError as e:
        logger.error(f"Vibin server start unsuccessful: {e}")
        logger.info("Vibin server start aborted")
        return

    @asynccontextmanager
    async def api_lifespan(app: FastAPI):
        """Handle the FastAPI lifecycle."""
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
        ws_connection_manager.shutdown()

        logger.info("Vibin server successfully shut down")

    # Information to help organize the OpenAPI documentation.
    tags_metadata = [
        {
            "name": "Vibin Server",
            "description": "Interact with the Vibin Server's top-level capabilities",
        },
        {
            "name": "Media System",
            "description": "Interact with devices in the media system as a whole "
            + "(Streamer, Media Server, Amplifier)",
        },
        {"name": "Artists", "description": "Interact with Media Server Artists"},
        {"name": "Albums", "description": "Interact with Media Server Albums"},
        {"name": "Tracks", "description": "Interact with Media Server Tracks"},
        {"name": "Browse", "description": "Browse media on the Media Server"},
        {"name": "Transport", "description": "Interact with the Streamer's transport"},
        {
            "name": "Active Playlist",
            "description": "Interact with the Streamer's Active Playlist",
        },
        {"name": "Presets", "description": "Interact with the Streamer's Presets"},
        {"name": "Stored Playlists", "description": "Interact with Stored Playlists"},
        {"name": "Favorites", "description": "Interact with Favorites"},
    ]

    # Create the vibin FastAPI application.
    vibin_app = FastAPI(
        title="vibin",
        description="REST API for the vibin backend. A WebSocket server is also available at `/ws`.",
        version=VIBIN_VER,
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

    # Redirect the root route to the UI static file router.
    @vibin_app.get("/", include_in_schema=False)
    def redirect_root_to_ui() -> Response:
        return RedirectResponse("/ui", status_code=303)

    vibin_app.include_router(ui_static_router)

    # -------------------------------------------------------------------------
    # Add the REST API routers.
    # -------------------------------------------------------------------------

    vibin_app.include_router(albums_router)
    vibin_app.include_router(artists_router)
    vibin_app.include_router(browse_router)
    vibin_app.include_router(favorites_router)
    vibin_app.include_router(playlist_router)
    vibin_app.include_router(presets_router)
    vibin_app.include_router(stored_playlists_router)
    vibin_app.include_router(system_router)
    vibin_app.include_router(tracks_router)
    vibin_app.include_router(transport_router)
    vibin_app.include_router(vibin_router)

    # -------------------------------------------------------------------------
    # Add the non-REST-API routers (media server proxy, WebSocket server, and
    # UPnP events callback).
    # -------------------------------------------------------------------------

    vibin_app.include_router(media_server_proxy_router)
    vibin_app.include_router(websocket_server_router)
    vibin_app.include_router(upnp_events_router)

    # -------------------------------------------------------------------------
    # Start the FastAPI application via uvicorn.
    # -------------------------------------------------------------------------

    logger.info(f"Starting REST API and WebSocket server")
    logger.info(f"REST API docs: http://{local_ip}:{port}{vibin_app.docs_url}")

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
