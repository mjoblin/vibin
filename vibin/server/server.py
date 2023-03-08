import asyncio
import json
from pathlib import Path
import socket
import time
import uuid
from typing import List, Optional, Union

from fastapi import FastAPI, Header, HTTPException, Response, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx  # TODO: Not in requirements; using to proxy to react on 3000
import starlette.requests
from starlette.endpoints import WebSocketEndpoint
import uvicorn
import xmltodict

from vibin import (
    Vibin,
    VibinDeviceError,
    VibinError,
    VibinNotFoundError,
    VibinMissingDependencyError,
)
from vibin.constants import VIBIN_PORT
from vibin.models import Album, Artist, Preset, StoredPlaylist, Track
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

    success = {
        "result": "success"
    }

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

    if vibinui:
        try:
            vibin_app.mount(
                "/ui/static",
                StaticFiles(directory=Path(vibinui, "static"), html=True),
                name="vibinui",
            )
        except RuntimeError as e:
            logger.error(f"Cannot serve UI: {e}")

    @vibin_app.router.get("/ui", include_in_schema=False)
    def serve_ui_root_index_html():
        return FileResponse(path=Path(vibinui, "index.html"))

    @vibin_app.router.get("/ui/{resource}", include_in_schema=False)
    def serve_ui_index_html(resource: str):
        if resource in [
            "current",
            "playlists",
            "artists",
            "albums",
            "tracks",
            "presets"
        ]:
            return FileResponse(path=Path(vibinui, "index.html"))

        return FileResponse(path=Path(vibinui, resource))

    # @vibin_app.get("/")
    # async def ui(response: Response):
    #     async with httpx.AsyncClient() as client:
    #         proxy = await client.get(f"http://{HOSTNAME}:3000")
    #         response.body = proxy.content
    #         response.status_code = proxy.status_code
    #
    #         return response

    # @vibin_app.get("/ui/{path:path}")
    # async def ui(path, response: Response):
    #     async with httpx.AsyncClient() as client:
    #         proxy = await client.get(f"http://{HOSTNAME}:3000/{path}")
    #         response.body = proxy.content
    #         response.status_code = proxy.status_code
    #
    #         return response

    # async def dev_ui(path, response: Response):
    #     async with httpx.AsyncClient() as client:
    #         proxy = await client.get(f"http://{HOSTNAME}:3000/{path}")
    #         response.body = proxy.content
    #         response.status_code = proxy.status_code
    #
    #         return response
    #
    # if vibinui == "dev":
    #     vibin_app.router.add_route(
    #         "/ui/{path:path}", dev_ui, methods=["GET"], include_in_schema=False
    #     )

    # @vibin_app.router.get("/ui/{resource}")
    # def serve_index_html(resource: str):
    #     # Intercept all UI calls and just return the index.html, allowing the
    #     # UI's router to handle any routes like /ui/albums.
    #     #
    #     # See "Correct default route usage":
    #     #   https://github.com/tiangolo/fastapi/discussions/9146
    #     return FileResponse(path=Path(vibinui, "index.html"))

    # -------------------------------------------------------------------------

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

    # TODO: Deprecate this? It uses (on CXNv2)
    #   _av_transport.GetCurrentTransportActions(), which reports actions that
    #   don't always seem correct (such as track skipping on internet radio).
    #   Use allowed_actions instead.
    @vibin_app.get("/transport/actions")
    async def transport_actions():
        return {
            "actions": vibin.transport_actions()
        }

    @vibin_app.get("/transport/active_controls")
    async def transport_active_controls():
        return {
            "actions": vibin.transport_active_controls()
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

    # TODO: Decide what to call this endpoint
    @vibin_app.get("/contents/{media_path:path}")
    async def path_contents(media_path) -> List:
        try:
            return vibin.media.get_path_contents(Path(media_path))
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/albums")
    async def albums() -> List[Album]:
        return vibin.media.albums

    @vibin_app.get("/albums/new")
    async def albums() -> List[Album]:
        return vibin.media.new_albums

    @vibin_app.get("/albums/{album_id}")
    def album_by_id(album_id: str):
        try:
            return vibin.media.album(album_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/albums/{album_id}/tracks")
    async def album_tracks(album_id: str) -> List[Album]:
        return vibin.media.album_tracks(album_id)

    @vibin_app.get("/albums/{album_id}/links")
    def album_links(album_id: str, all_types: bool = False):
        return vibin.media_links(album_id, all_types)

    @vibin_app.get("/artists")
    async def albums() -> List[Artist]:
        return vibin.media.artists

    @vibin_app.get("/artists/{artist_id}")
    def artist_by_id(artist_id: str):
        try:
            return vibin.media.artist(artist_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/tracks")
    async def tracks() -> List[Track]:
        return vibin.media.tracks

    @vibin_app.get("/tracks/{track_id}")
    def track_by_id(track_id: str):
        try:
            return vibin.media.track(track_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/tracks/{track_id}/lyrics")
    def track_lyrics(track_id: str):
        lyrics = vibin.lyrics_for_track(track_id)

        if lyrics is None:
            raise HTTPException(status_code=404, detail="Lyrics not found")

        return lyrics

    @vibin_app.get("/tracks/{track_id}/waveform.png")
    def track_waveform_png(
            track_id: str,
            width: int = 800,
            height: int = 250,
    ):
        try:
            waveform = vibin.waveform_for_track(
                track_id, data_format="png", width=width, height=height
            )

            return Response(content=waveform, media_type="image/png")
        except VibinMissingDependencyError as e:
            # TODO: Where possible, have errors reference docs for possible
            #   actions the caller can take to resolve the issue.
            logger.warning(
                f"Cannot generate waveform due to missing dependency: {e}"
            )

            raise HTTPException(
                status_code=404,
                detail=f"Cannot generate waveform due to missing dependency: {e}",
            )

    @vibin_app.get("/tracks/{track_id}/waveform")
    def track_waveform(
            track_id: str,
            width: int = 800,
            height: int = 250,
            accept: Union[str, None] = Header(default="application/json"),
    ):
        # TODO: This waveform_format / media_type / "accept" header stuff
        #   feels too convoluted.
        waveform_format = "json"
        media_type = "application/json"

        if accept == "application/octet-stream":
            waveform_format = "dat"
            media_type = "application/octet-stream"
        elif accept == "image/png":
            waveform_format = "png"
            media_type = "image/png"

        try:
            if waveform_format == "png":
                waveform = vibin.waveform_for_track(
                    track_id, data_format=waveform_format, width=width, height=height
                )
            else:
                waveform = vibin.waveform_for_track(track_id, data_format=waveform_format)

            return Response(
                content=json.dumps(waveform) if waveform_format == "json" else waveform,
                media_type=media_type,
            )
        except VibinMissingDependencyError as e:
            # TODO: Where possible, have errors reference docs for possible
            #   actions the caller can take to resolve the issue.
            raise HTTPException(
                status_code=404,
                detail=f"Cannot generate waveform due to missing dependency: {e}",
            )

    @vibin_app.get("/tracks/{track_id}/links")
    def track_links(track_id: str, all_types: bool = False):
        return vibin.media_links(track_id, all_types)

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

    @vibin_app.post("/playlist/play/id/{playlist_entry_id}")
    async def playlist_play_id(playlist_entry_id: int):
        return vibin.streamer.play_playlist_id(playlist_entry_id)

    @vibin_app.post("/playlist/play/index/{index}")
    async def playlist_play_index(index: int):
        return vibin.streamer.play_playlist_index(index)

    @vibin_app.post("/playlist/clear")
    async def playlist_clear():
        return vibin.streamer.playlist_clear()

    @vibin_app.post("/playlist/delete/{playlist_entry_id}")
    async def playlist_delete_entry(playlist_entry_id: int):
        return vibin.streamer.playlist_delete_entry(playlist_entry_id)

    @vibin_app.post("/playlist/move/{playlist_entry_id}")
    async def playlist_move_entry(playlist_entry_id: int, from_index: int, to_index: int):
        return vibin.streamer.playlist_move_entry(playlist_entry_id, from_index, to_index)

    @vibin_app.get("/playlists")
    def playlists() -> list[StoredPlaylist]:
        return vibin.playlists()

    @vibin_app.get("/playlists/{playlist_id}")
    def playlists_id(playlist_id: str) -> StoredPlaylist:
        playlist = vibin.get_playlist(playlist_id)

        if playlist is None:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

        return playlist

    @vibin_app.put("/playlists/{playlist_id}")
    def playlists_id_update(playlist_id: str, name: Optional[str] = None) -> StoredPlaylist:
        metadata = {"name": name} if name else None

        try:
            return vibin.update_playlist_metadata(
                playlist_id=playlist_id, metadata=metadata
            )
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

    @vibin_app.delete("/playlists/{playlist_id}", status_code=204)
    def playlists_id_delete(playlist_id: str):
        try:
            vibin.delete_playlist(playlist_id=playlist_id)
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )

    @vibin_app.post("/playlists/{playlist_id}/make_current")
    def playlists_id_make_current(playlist_id: str) -> StoredPlaylist:
        # TODO: Is it possible to configure FastAPI to always treat
        #   VibinNotFoundError as a 404 and VibinDeviceError as a 503?
        try:
            return vibin.set_current_playlist(playlist_id)
        except VibinNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Playlist not found: {playlist_id}"
            )
        except VibinDeviceError as e:
            raise HTTPException(
                status_code=503, detail=f"Downstream device error: {e}"
            )

    @vibin_app.post("/playlists/current/store")
    def playlists_current_store(
            name: Optional[str] = None, replace: Optional[bool] = True
    ):
        metadata = {"name": name} if name else None

        return vibin.store_current_playlist(metadata=metadata, replace=replace)

    @vibin_app.get("/presets")
    def presets() -> list[Preset]:
        return vibin.presets

    @vibin_app.post("/presets/{preset_id}/play")
    def preset_play(preset_id: int):
        return vibin.streamer.play_preset_id(preset_id)

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

            await websocket.send_text(self.build_message(
                json.dumps(vibin.streamer.transport_active_controls()),
                "ActiveTransportControls",
            ))

            await websocket.send_text(self.build_message(
                json.dumps(vibin.presets), "Presets")
            )

            await websocket.send_text(self.build_message(
                json.dumps(vibin.stored_playlist_details), "StoredPlaylists")
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
            elif messageType == "ActiveTransportControls":
                message["payload"] = data_as_dict
            elif messageType == "Presets":
                message["payload"] = data_as_dict
            elif messageType == "StoredPlaylists":
                message["payload"] = data_as_dict

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
