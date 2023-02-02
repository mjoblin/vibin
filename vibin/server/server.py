import asyncio
import json
import socket
import time
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx  # TODO: Not in requirements; using to proxy to react on 3000
import starlette.requests
from starlette.endpoints import WebSocketEndpoint
import uvicorn
import xmltodict

from vibin import Vibin, VibinError
from vibin.constants import VIBIN_PORT
from vibin.models import Album
from vibin.streamers import SeekTarget
from vibin.logger import logger


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
            proxy = await client.get(f"http://{HOSTNAME}:3000")
            response.body = proxy.content
            response.status_code = proxy.status_code

            return response

    @vibin_app.get("/ui/{path:path}")
    async def ui(path, response: Response):
        async with httpx.AsyncClient() as client:
            proxy = await client.get(f"http://{HOSTNAME}:3000/{path}")
            response.body = proxy.content
            response.status_code = proxy.status_code

            return response

    # TODO: Do we want /system endpoints for both streamer and media?
    @vibin_app.post("/system/power/toggle")
    async def system_power_toggle():
        try:
            vibin.streamer.power_toggle()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

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

    # TODO: Consider whether repeat and shuffle should be toggles or not.
    @vibin_app.post("/transport/repeat")
    async def transport_repeat():
        vibin.repeat("toggle")

    @vibin_app.post("/transport/shuffle")
    async def transport_shuffle():
        vibin.shuffle("toggle")

    @vibin_app.post("/transport/seek")
    async def transport_seek(target: SeekTarget):
        vibin.seek(target)

    @vibin_app.get("/transport/position")
    async def transport_position():
        return {
            "position": vibin.transport_position()
        }

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

    @vibin_app.get("/albums/{album_id}/tracks")
    async def album_tracks(album_id: str) -> List[Album]:
        return vibin.media.tracks(album_id)

    @vibin_app.get("/albums/{album_id}/links")
    async def album_links(album_id: str, all_types: bool = False):
        return vibin.album_links(album_id, all_types)

    @vibin_app.get("/tracks/{track_id}/lyrics")
    async def album_tracks(track_id: str):
        lyrics = vibin.lyrics_for_track(track_id)

        if lyrics is None:
            raise HTTPException(status_code=404, detail="Lyrics not found")

        return lyrics

    @vibin_app.get("/tracks/{track_id}/links")
    async def track_links(track_id: str, all_types: bool = False):
        return vibin.track_links(track_id, all_types)

    @vibin_app.get("/playlist")
    async def playlist():
        return vibin.streamer.playlist()

    @vibin_app.post("/playlist/modify/{media_id}")
    async def playlist_modify(
            media_id: str,
            action: str = "REPLACE",
            insert_index: Optional[int] = None,
    ):
        return vibin.modify_playlist(media_id, action, insert_index)

    @vibin_app.post("/playlist/play/id/{playlist_id}")
    async def playlist_play_id(playlist_id: int):
        return vibin.streamer.play_playlist_id(playlist_id)

    @vibin_app.post("/playlist/play/index/{index}")
    async def playlist_play_index(index: int):
        return vibin.streamer.play_playlist_index(index)

    @vibin_app.post("/playlist/clear")
    async def playlist_clear():
        return vibin.streamer.playlist_clear()

    @vibin_app.post("/playlist/delete/{playlist_id}")
    async def playlist_delete_item(playlist_id: int):
        return vibin.streamer.playlist_delete_item(playlist_id)

    @vibin_app.post("/playlist/move/{playlist_id}")
    async def playlist_move_item(playlist_id: int, from_index: int, to_index: int):
        return vibin.streamer.playlist_move_item(playlist_id, from_index, to_index)

    @vibin_app.get("/browse/{parent_id}")
    async def browse(parent_id: str):
        return vibin.browse_media(parent_id)

    @vibin_app.get("/metadata/{id}")
    async def browse(id: str):
        return xmltodict.parse(vibin.media.get_metadata(id))

    @vibin_app.post("/subscribe")
    async def transport_play_media_id():
        vibin.subscribe()

    @vibin_app.get("/statevars")
    async def state_vars() -> dict:
        return vibin.state_vars

    @vibin_app.get("/playstate")
    async def play_state() -> dict:
        return vibin.play_state

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

            # TODO: Clean up using "state_vars_handler" for both state_vars
            #   (UPnP) updates and websocket updates.
            vibin.on_state_vars_update(self.state_vars_handler)
            vibin.on_websocket_update(self.websocket_update_handler)

        async def on_connect(self, websocket: WebSocket) -> None:
            await websocket.accept()
            client_ip, client_port = websocket.client
            logger.info(
                f"Websocket connection accepted from {client_ip}:{client_port}"
            )
            self.sender_task = asyncio.create_task(self.sender(websocket))

            await websocket.send_text(
                # TODO: Fix this hack which encforces streamer-system-status
                #    (ignoring system_state["media_device"]).
                self.build_message(
                    json.dumps(vibin.system_state["streamer"]), "System"
                )
            )

            # Send initial state to new client connection.
            await websocket.send_text(
                self.build_message(json.dumps(vibin.state_vars), "StateVars")
            )

            await websocket.send_text(
                self.build_message(json.dumps(vibin.play_state), "PlayState")
            )

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

        def build_message(self, data: str, messageType: str) -> str:
            data_as_dict = json.loads(data)

            message = {
                "id": str(uuid.uuid4()),
                "time": int(time.time() * 1000),
                "type": messageType,
            }

            # TODO: This (the streamer- and media-server-agnostic layer)
            #   shouldn't have any awareness of the CXNv2 data shapes. So the
            #   ["data"]["params"] stuff below should be abstracted away.

            if messageType == "System":
                # TODO: Fix this hack. We're assuming we're getting a streamer
                #   system update, but it might be a media_source update.
                message["payload"] = {
                    "streamer": data_as_dict,
                }
            elif messageType == "StateVars":
                message["payload"] = data_as_dict
            elif messageType == "PlayState" or messageType == "Position":
                try:
                    message["payload"] = data_as_dict["params"]["data"]
                except KeyError:
                    # TODO: Add proper error handling support.
                    message["payload"] = {}

            return json.dumps(message)

        async def sender(self, websocket: WebSocket) -> None:
            while True:
                to_send = await self.state_vars_queue.get()
                to_send_dict = json.loads(to_send)

                # TODO: All the json.loads()/dumps() down the path from the
                #   source through the queue and into the message builder is
                #   all a bit much -- most of it can probably be avoided.
                await websocket.send_text(
                    self.build_message(json.dumps(to_send_dict["data"]), to_send_dict["type"])
                )

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
