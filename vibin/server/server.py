import asyncio
import socket
from typing import List

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx  # TODO: Not in requirements; using to proxy to react on 3000
import starlette.requests
from starlette.endpoints import WebSocketEndpoint
import uvicorn

from vibin import Vibin, VibinError
from vibin.constants import VIBIN_PORT
from vibin.models import Album
from vibin.streamers import SeekTarget
from vibin.logger import logger


UPNP_EVENTS_BASE_ROUTE = "/upnpevents"


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
        streamer="streamer",
        media="Asset UPnP: thicc",
        discovery_timeout=5,
        vibinui=None,
):
    local_ip = get_local_ip() if host == "0.0.0.0" else host

    # TODO: This could be in a FastAPI on_startup handler.
    vibin = Vibin(
        streamer=streamer,
        media=media,
        discovery_timeout=discovery_timeout,
        subscribe_callback_base=f"http://{local_ip}:{port}{UPNP_EVENTS_BASE_ROUTE}",
    )

    logger.info("Starting server")
    vibin_app = FastAPI()

    if vibinui:
        try:
            vibin_app.mount(
                "/ui",
                StaticFiles(directory=vibinui, html=True),
                name="vibinui",
            )
        except RuntimeError as e:
            logger.error(f"Cannot serve UI: {e}")

    success = {
        "result": "success"
    }

    # @vibin_app.get("/")
    # async def redirect():
    #     return RedirectResponse(url="http://10.0.0.3:3000")
    #     # return RedirectResponse(url="/ui")

    @vibin_app.get("/")
    async def ui(response: Response):
        async with httpx.AsyncClient() as client:
            proxy = await client.get("http://10.0.0.3:3000")
            response.body = proxy.content
            response.status_code = proxy.status_code

            return response

    @vibin_app.get("/ui/{path:path}")
    async def ui(path, response: Response):
        async with httpx.AsyncClient() as client:
            proxy = await client.get(f"http://10.0.0.3:3000/{path}")
            response.body = proxy.content
            response.status_code = proxy.status_code

            return response

    @vibin_app.post("/transport/pause")
    async def transport_pause():
        try:
            vibin.pause()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @vibin_app.post("/transport/play")
    async def transport_play():
        try:
            vibin.play()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @vibin_app.post("/transport/next")
    async def transport_next():
        try:
            vibin.next_track()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @vibin_app.post("/transport/previous")
    async def transport_previous():
        vibin.previous_track()

    @vibin_app.post("/transport/seek")
    async def transport_seek(target: SeekTarget):
        vibin.seek(target)

    @vibin_app.post("/transport/play/{media_id}")
    async def transport_play_media_id(media_id: str):
        vibin.play_id(media_id)

    @vibin_app.get("/transport/actions")
    async def transport_actions():
        return {
            "actions": vibin.transport_actions()
        }

    @vibin_app.get("/transport/state")
    async def transport_state():
        return {
            "state": vibin.transport_state(),
        }

    @vibin_app.get("/transport/status")
    async def transport_status():
        return {
            "status": vibin.transport_status(),
        }

    @vibin_app.get("/albums")
    async def albums() -> List[Album]:
        return vibin.media.albums

    @vibin_app.get("/playlist")
    async def playlist():
        return vibin.streamer.playlist()

    @vibin_app.post("/playlist/play/id/{playlist_id}")
    async def playlist_play_id(playlist_id: int):
        return vibin.streamer.play_playlist_id(playlist_id)

    @vibin_app.post("/playlist/play/index/{index}")
    async def playlist_play_index(index: int):
        return vibin.streamer.play_playlist_index(index)

    @vibin_app.get("/browse/{parent_id}")
    async def browse(parent_id: str):
        return vibin.browse_media(parent_id)

    @vibin_app.post("/subscribe")
    async def transport_play_media_id():
        vibin.subscribe()

    @vibin_app.get("/statevars")
    async def state_vars() -> dict:
        return vibin.state_vars

    @vibin_app.api_route(
        UPNP_EVENTS_BASE_ROUTE + "/{service}",
        methods=["NOTIFY"],
    )
    async def listen(service: str, request: starlette.requests.Request) -> None:
        body = await request.body()
        vibin.upnp_event(service, body.decode("utf-8"))

    @vibin_app.websocket_route("/ws")
    class WebSocketTicks(WebSocketEndpoint):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.state_vars_queue = asyncio.Queue()
            self.sender_task = None

            vibin.on_state_vars_update(self.state_vars_handler)

        async def on_connect(self, websocket: WebSocket) -> None:
            await websocket.accept()
            client_ip, client_port = websocket.client
            logger.info(
                f"Websocket connection accepted from {client_ip}:{client_port}"
            )
            self.sender_task = asyncio.create_task(self.sender(websocket))
            await websocket.send_json(vibin.state_vars)

        async def on_disconnect(
                self, websocket: WebSocket, close_code: int
        ) -> None:
            self.sender_task.cancel()
            client_ip, client_port = websocket.client
            logger.info(
                f"Websocket connection closed [{close_code}] for client " +
                f"{client_ip}:{client_port}"
            )

        def state_vars_handler(self, data: str):
            self.state_vars_queue.put_nowait(item=data)

        async def sender(self, websocket: WebSocket) -> None:
            while True:
                state = await self.state_vars_queue.get()
                await websocket.send_text(state)

    @vibin_app.on_event("shutdown")
    def shutdown_event():
        vibin.shutdown()

    # -------------------------------------------------------------------------

    uvicorn.run(
        vibin_app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    server_start()
