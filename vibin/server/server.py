import asyncio
from contextlib import asynccontextmanager
import functools
import json
import os
from pathlib import Path
import platform
import socket
import time
import uuid

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
import starlette.requests
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.endpoints import WebSocketEndpoint
import uvicorn

from vibin import Vibin, VibinError
from vibin.constants import VIBIN_PORT
from vibin.models import ServerStatus, WebSocketClientDetails
from vibin.server.routers import (
    albums_router,
    artists_router,
    browse_router,
    favorites_router,
    playlist_router,
    playlists_router,
    presets_router,
    system_router,
    tracks_router,
    transport_router,
    vibin_router,
)
from vibin.logger import logger
from vibin.utils import replace_media_server_urls_with_proxy
from .websocket_server import VibinWebSocketEndpoint
from .websocket_server_three import websocket_endpoint_three

UPNP_EVENTS_BASE_ROUTE = "/upnpevents"
HOSTNAME = "192.168.1.30"


def get_local_ip():
    # https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()

    return ip


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
        vibin = Vibin(
            streamer=streamer,
            media=media,
            discovery_timeout=discovery_timeout,
            subscribe_callback_base=f"http://{local_ip}:{port}{UPNP_EVENTS_BASE_ROUTE}",
        )
    except VibinError as e:
        logger.error(f"Vibin server start unsuccessful: {e}")
        return

    media_server_proxy_client = None
    media_server_proxy_target = None

    # Configure art reverse proxy to media server
    if proxy_media_server:
        if vibin.media is not None:
            media_server_proxy_target = vibin.media.url_prefix
            media_server_proxy_client = httpx.AsyncClient(
                base_url=media_server_proxy_target
            )

            logger.info(f"Proxying art at /proxy (target: {media_server_proxy_target})")
        else:
            error = "Unable to proxy art; media server not located"
            logger.error(error)
            vibin.shutdown()

            raise VibinError(error)

    def transform_media_server_urls_if_proxying(func):
        @functools.wraps(func)
        def wrapper_transform_media_server_urls_if_proxying(*args, **kwargs):
            if proxy_media_server:
                return replace_media_server_urls_with_proxy(
                    func(*args, **kwargs), media_server_proxy_target
                )

            return func(*args, **kwargs)

        return wrapper_transform_media_server_urls_if_proxying

    @asynccontextmanager
    async def api_lifespan(app: FastAPI):
        yield

        logger.info("Vibin server shutdown requested")
        vibin.shutdown()

        if media_server_proxy_client is not None:
            logger.info("Shutting down media server proxy")
            await media_server_proxy_client.aclose()

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

    start_time = time.time()
    connected_websockets = {}

    success = {"result": "success"}

    def requires_media(func):
        @functools.wraps(func)
        def wrapper_requires_media(*args, **kwargs):
            if vibin.media is None:
                raise HTTPException(
                    status_code=404,
                    detail="Feature unavailable (no local media server registered with Vibin)",
                )

            return func(*args, **kwargs)

        return wrapper_requires_media

    # -------------------------------------------------------------------------
    # Experiments in proxying the UI for both production and dev.
    # -------------------------------------------------------------------------

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

    @vibin_app.post("/subscribe", include_in_schema=False)
    def transport_play_media_id():
        vibin.subscribe()

    @vibin_app.get("/proxy/{path:path}", include_in_schema=False)
    async def art_proxy(request: Request):
        if not proxy_media_server:
            raise HTTPException(
                status_code=404,
                detail="Art proxy is not enabled; see 'vibin serve --proxy-art'",
            )

        if media_server_proxy_client is None:
            raise HTTPException(
                status_code=500,
                detail="Art proxy was unable to be configured",
            )

        url = httpx.URL(
            path=request.path_params["path"], query=request.url.query.encode("utf-8")
        )

        proxy_request = media_server_proxy_client.build_request(
            request.method,
            url,
            headers=request.headers.raw,
            content=await request.body(),
            timeout=20.0,
        )

        try:
            proxy_response = await media_server_proxy_client.send(
                proxy_request, stream=True
            )
        except httpx.TimeoutException:
            logger.warning(f"Proxy timed out on request: {request.url}")

        return StreamingResponse(
            proxy_response.aiter_raw(),
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
            background=BackgroundTask(proxy_response.aclose),
        )

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

    # vibin_app.add_websocket_route("/ws", wtf)

    # vibin_app.add_websocket_route("/ws", WebSocketTicks)
    # vibin_app.add_websocket_route("/ws", wtf)
    # vibin_app.add_api_websocket_route("/ws", wtf)
    # vibin_app.add_api_websocket_route("/ws", vibin_websocket_server)

    # vibin_app.add_api_websocket_route("/ws", websocket_endpoint_three)

    @vibin_app.websocket_route("/ws")
    class WebSocketTicks(WebSocketEndpoint):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.state_vars_queue = asyncio.Queue()
            self.sender_task = None

            # TODO: Clean up using "state_vars_handler" for both state_vars
            #   (UPnP) updates and websocket updates.
            vibin.on_state_vars_update(self.state_vars_handler)
            vibin.on_websocket_update(self.websocket_update_handler)

        async def on_connect(self, websocket: WebSocket) -> None:
            await websocket.accept()
            client_ip, client_port = websocket.client

            connected_websockets[f"{client_ip}:{client_port}"] = {
                "id": str(uuid.uuid4()),
                "when_connected": time.time(),
                "websocket": websocket,
            }

            logger.info(f"WebSocket connection accepted from {client_ip}:{client_port}")
            self.sender_task = asyncio.create_task(self.sender(websocket))

            # TODO: Fix this hack which encforces streamer-system-status
            #    (ignoring system_state["media_device"]).
            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.system_state),
                    "System",
                    websocket,
                )
            )

            # Send initial state to new client connection.
            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.state_vars),
                    "StateVars",
                    websocket,
                )
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.play_state),
                    "PlayState",
                    websocket,
                )
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.streamer.transport_active_controls()),
                    "ActiveTransportControls",
                    websocket,
                )
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.streamer.device_display),
                    "DeviceDisplay",
                    websocket,
                )
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.favorites()), "Favorites", websocket
                )
            )

            await websocket.send_text(
                self.build_message(json.dumps(vibin.presets), "Presets", websocket)
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(vibin.stored_playlist_details),
                    "StoredPlaylists",
                    websocket,
                )
            )

            await websocket.send_text(
                self.build_message(
                    json.dumps(server_status().dict()), "VibinStatus", websocket
                )
            )

            # TODO: Allow the server to send a message to all connected
            #   websockets. Perhaps just make _websocket_message_handler more
            #   publicly accessible.
            vibin._websocket_message_handler("VibinStatus", json.dumps(server_status().dict()))

        async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:
            self.sender_task.cancel()
            client_ip, client_port = websocket.client

            try:
                del connected_websockets[f"{client_ip}:{client_port}"]
            except KeyError:
                pass

            vibin._websocket_message_handler("VibinStatus", json.dumps(server_status().dict()))

            logger.info(
                f"WebSocket connection closed [{close_code}] for client "
                + f"{client_ip}:{client_port}"
            )

        def state_vars_handler(self, data: str):
            self.state_vars_queue.put_nowait(
                item=json.dumps({"type": "StateVars", "data": json.loads(data)})
            )

        def websocket_update_handler(self, message_type: str, data: str):
            # TODO: Don't override state_vars queue for both state vars and
            #   websocket updates.
            self.state_vars_queue.put_nowait(
                # item=json.dumps({"type": "PlayState", "data": json.loads(data)})
                item=json.dumps({"type": message_type, "data": json.loads(data)})
            )

        def inject_id(self, data: str):
            data_dict = json.loads(data)
            data_dict["id"] = str(uuid.uuid4())

            return json.dumps(data_dict)

        def build_message(
            self, data: str, messageType: str, client_ws: WebSocket = None
        ) -> str:
            data_as_dict = json.loads(data)

            this_client = next(
                (
                    client
                    for client in connected_websockets.values()
                    if client["websocket"] == client_ws
                ),
                None,
            )

            message = {
                "id": str(uuid.uuid4()),
                "client_id": this_client["id"],
                "time": int(time.time() * 1000),
                "type": messageType,
            }

            # TODO: This (the streamer- and media-server-agnostic layer)
            #   shouldn't have any awareness of the CXNv2 data shapes. So the
            #   ["data"]["params"] stuff below should be abstracted away.

            if messageType == "System":
                # TODO: Fix this hack. We're assuming we're getting a streamer
                #   system update, but it might be a media_source update.
                # message["payload"] = {
                #     "streamer": data_as_dict,
                # }
                #
                # TODO UPDATE: We now ignore the incoming data and just emit a
                #   full system_state payload.
                message["payload"] = vibin.system_state
            elif messageType == "StateVars":
                message["payload"] = data_as_dict
            elif messageType == "PlayState" or messageType == "Position":
                try:
                    message["payload"] = data_as_dict["params"]["data"]
                except KeyError:
                    # TODO: Add proper error handling support.
                    message["payload"] = {}
            elif messageType == "ActiveTransportControls":
                message["payload"] = data_as_dict
            elif messageType == "DeviceDisplay":
                message["payload"] = data_as_dict
            elif messageType == "Presets":
                message["payload"] = data_as_dict
            elif messageType == "StoredPlaylists":
                message["payload"] = data_as_dict
            elif messageType == "Favorites":
                message["payload"] = {
                    "favorites": data_as_dict,
                }
            elif messageType == "VibinStatus":
                message["payload"] = data_as_dict

            # Some messages contain media server urls that we may want to proxy.
            if proxy_media_server and messageType in [
                "DeviceDisplay",
                "Favorites",
                "PlayState",
                "Presets",
                "StateVars",
            ]:
                message = replace_media_server_urls_with_proxy(
                    message, media_server_proxy_target
                )

            return json.dumps(message)

        async def sender(self, websocket: WebSocket) -> None:
            while True:
                to_send = await self.state_vars_queue.get()
                to_send_dict = json.loads(to_send)

                # TODO: All the json.loads()/dumps() down the path from the
                #   source through the queue and into the message builder is
                #   all a bit much -- most of it can probably be avoided.
                await websocket.send_text(
                    self.build_message(
                        json.dumps(to_send_dict["data"]),
                        to_send_dict["type"],
                        websocket,
                    )
                )

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
