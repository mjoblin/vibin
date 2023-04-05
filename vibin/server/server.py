import asyncio
from contextlib import asynccontextmanager
import functools
import json
import os
from pathlib import Path
import platform
import socket
import time
from typing import List, Optional, Union
import uuid

from fastapi import FastAPI, Header, HTTPException, Response, WebSocket
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
import xmltodict

from vibin import (
    Vibin,
    VibinDeviceError,
    VibinError,
    VibinNotFoundError,
    VibinMissingDependencyError,
)
from vibin.constants import VIBIN_PORT
from vibin.models import (
    Album,
    Artist,
    Favorite,
    LyricsQuery,
    PlaylistModifyPayload,
    StoredPlaylist,
    Track,
)
from vibin.streamers import SeekTarget
from vibin.logger import logger
from vibin.utils import replace_media_server_urls_with_proxy


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

    if not os.path.exists(vibinui):
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
            media_server_proxy_client = \
                httpx.AsyncClient(base_url=media_server_proxy_target)

            logger.info(
                f"Proxying art at /proxy (target: {media_server_proxy_target})"
            )
        else:
            error = "Unable to proxy art; media server not located"
            logger.error(error)
            vibin.shutdown()

            raise VibinError(error)

    def transform_media_server_urls_if_proxying(func):
        @functools.wraps(func)
        async def wrapper_transform_media_server_urls_if_proxying(*args, **kwargs):
            if proxy_media_server:
                return replace_media_server_urls_with_proxy(
                    await func(*args, **kwargs), media_server_proxy_target
                )

            return await func(*args, **kwargs)

        return wrapper_transform_media_server_urls_if_proxying

    @asynccontextmanager
    async def api_lifespan(app: FastAPI):
        yield

        logger.info("Vibin server shutdown requested")
        vibin.shutdown()
        logger.info("Shutting down media server proxy")
        await media_server_proxy_client.aclose()
        logger.info("Vibin server successfully shut down")

    vibin_app = FastAPI(lifespan=api_lifespan)

    start_time = time.time()
    connected_websockets = {}

    success = {
        "result": "success"
    }

    # TODO: Clean up sync/async in general, including these two decorators

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

    def requires_media_async(func):
        @functools.wraps(func)
        async def wrapper_requires_media_async(*args, **kwargs):
            if vibin.media is None:
                raise HTTPException(
                    status_code=404,
                    detail="Feature unavailable (no local media server registered with Vibin)",
                )

            return await func(*args, **kwargs)

        return wrapper_requires_media_async

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

            logger.info(f"Web UI available at http://{local_ip}:{port}/ui")
        except RuntimeError as e:
            logger.error(f"Cannot serve Web UI: {e}")

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

    def server_status():
        clients = []

        for websocket_info in connected_websockets.values():
            client_ip, client_port = websocket_info["websocket"].client

            clients.append({
                "id": websocket_info["id"],
                "when_connected": websocket_info["when_connected"],
                "ip": client_ip,
                "port": client_port,
            })

        return {
            "start_time": start_time,
            "system_node": platform.node(),
            "system_platform": platform.platform(),
            "system_version": platform.version(),
            "clients": clients,
        }

    @vibin_app.get("/vibin/status")
    def vibin_status():
        return server_status()

    @vibin_app.post("/vibin/clear_media_caches")
    @requires_media
    def vibin_clear_media_caches():
        return vibin.media.clear_caches()

    # TODO: Do we want /system endpoints for both streamer and media?
    @vibin_app.post("/system/power/toggle")
    async def system_power_toggle():
        try:
            vibin.streamer.power_toggle()
            return success
        except VibinError as e:
            raise HTTPException(status_code=500, detail=f"{e}")

    @vibin_app.post("/system/source")
    async def system_source(source: str):
        try:
            vibin.streamer.set_source(source)
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
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def path_contents(media_path) -> List:
        try:
            return vibin.media.get_path_contents(Path(media_path))
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/albums")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def albums() -> List[Album]:
        return vibin.media.albums

    @vibin_app.get("/albums/new")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def albums_new() -> List[Album]:
        return vibin.media.new_albums

    @vibin_app.get("/albums/{album_id}")
    @transform_media_server_urls_if_proxying
    @requires_media
    async def album_by_id(album_id: str) -> Album:
        try:
            return vibin.media.album(album_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/albums/{album_id}/tracks")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def album_tracks(album_id: str) -> List[Track]:
        return vibin.media.album_tracks(album_id)

    @vibin_app.get("/albums/{album_id}/links")
    @requires_media
    def album_links(album_id: str, all_types: bool = False):
        return vibin.media_links(album_id, all_types)

    @vibin_app.get("/artists")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def artists() -> List[Artist]:
        return vibin.media.artists

    @vibin_app.get("/artists/{artist_id}")
    @transform_media_server_urls_if_proxying
    @requires_media
    async def artist_by_id(artist_id: str):
        try:
            return vibin.media.artist(artist_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/tracks")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def tracks() -> List[Track]:
        return vibin.media.tracks

    @vibin_app.get("/tracks/lyrics")
    def track_lyrics(
            artist: str, title: str, update_cache: Optional[bool] = False
    ):
        lyrics = vibin.lyrics_for_track(
            artist=artist, title=title, update_cache=update_cache
        )

        if lyrics is None:
            raise HTTPException(status_code=404, detail="Lyrics not found")

        return lyrics

    @vibin_app.post("/tracks/lyrics/validate")
    def track_lyrics_validate(artist: str, title: str, is_valid: bool):
        lyrics = track_lyrics(artist=artist, title=title)
        vibin.lyrics_valid(lyrics_id=lyrics["id"], is_valid=is_valid)

        return track_lyrics(artist=artist, title=title)

    @vibin_app.post("/tracks/lyrics/search")
    def track_lyrics_search(lyrics_query: LyricsQuery):
        results = vibin.lyrics_search(lyrics_query.query)

        return {
            "query": lyrics_query.query,
            "matches": results,
        }

    @vibin_app.get("/tracks/links")
    def track_links(
            artist: Optional[str],
            album: Optional[str],
            title: Optional[str],
            all_types: bool = False
    ):
        return vibin.media_links(
            artist=artist, album=album, title=title, include_all=all_types
        )

    @vibin_app.get("/tracks/{track_id}")
    @transform_media_server_urls_if_proxying
    @requires_media
    async def track_by_id(track_id: str) -> Track:
        try:
            return vibin.media.track(track_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.get("/tracks/{track_id}/lyrics")
    def track_lyrics_by_track_id(
            track_id: str, update_cache: Optional[bool] = False
    ):
        lyrics = vibin.lyrics_for_track(
            track_id=track_id, update_cache=update_cache
        )

        if lyrics is None:
            raise HTTPException(status_code=404, detail="Lyrics not found")

        return lyrics

    @vibin_app.post("/tracks/{track_id}/lyrics/validate")
    def track_lyrics_by_track_id_validate(track_id: str, is_valid: bool):
        lyrics = track_lyrics_by_track_id(track_id)
        vibin.lyrics_valid(lyrics_id=lyrics.lyrics_id, is_valid=is_valid)

        return track_lyrics_by_track_id(track_id)

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
    def track_links_by_track_id(track_id: str, all_types: bool = False):
        return vibin.media_links(media_id=track_id, include_all=all_types)

    @vibin_app.get("/playlist")
    @transform_media_server_urls_if_proxying
    async def playlist():
        return vibin.streamer.playlist()

    @vibin_app.post("/playlist/modify")
    async def playlist_modify_multiple_entries(payload: PlaylistModifyPayload):
        if payload.action != "REPLACE":
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported action: {payload.action}. Supported actions: REPLACE.",
            )

        return vibin.play_ids(payload.media_ids, max_count=payload.max_count)

    @vibin_app.post("/playlist/modify/{media_id}")
    async def playlist_modify_single_entry(
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

    @vibin_app.post("/playlist/play/favorites/albums")
    async def playlist_play_favorite_albums(max_count: int = 10):
        return vibin.play_favorite_albums(max_count=max_count)

    @vibin_app.post("/playlist/play/favorites/tracks")
    async def playlist_play_favorite_tracks(max_count: int = 100):
        return vibin.play_favorite_tracks(max_count=max_count)

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

    @vibin_app.get("/favorites")
    @transform_media_server_urls_if_proxying
    async def favorites():
        return {
            "favorites": vibin.favorites(),
        }

    @vibin_app.get("/favorites/albums")
    @transform_media_server_urls_if_proxying
    async def favorites_albums():
        favorites = vibin.favorites(requested_types=["album"])

        return {
            "favorites": favorites,
        }

    @vibin_app.get("/favorites/tracks")
    @transform_media_server_urls_if_proxying
    async def favorites_tracks():
        favorites = vibin.favorites(requested_types=["track"])

        return {
            "favorites": favorites,
        }

    @vibin_app.post("/favorites")
    async def favorites_create(favorite: Favorite):
        try:
            return vibin.store_favorite(favorite.type, favorite.media_id)
        except VibinNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @vibin_app.delete("/favorites/{media_id}")
    async def favorites_delete(media_id):
        vibin.delete_favorite(media_id)

    @vibin_app.get("/presets")
    @transform_media_server_urls_if_proxying
    async def presets() -> dict:
        return vibin.presets

    @vibin_app.post("/presets/{preset_id}/play")
    def preset_play(preset_id: int):
        return vibin.streamer.play_preset_id(preset_id)

    @vibin_app.get("/browse/{parent_id}")
    @transform_media_server_urls_if_proxying
    @requires_media_async
    async def browse(parent_id: str):
        return vibin.browse_media(parent_id)

    @vibin_app.get("/metadata/{id}")
    @transform_media_server_urls_if_proxying
    @requires_media_async
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

    @vibin_app.get("/devicedisplay")
    async def device_display() -> dict:
        return vibin.device_display

    @vibin_app.get("/db")
    async def db_get():
        return vibin.db_get()

    @vibin_app.put("/db")
    async def db_set(data: dict):
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

    @vibin_app.get("/proxy/{path:path}")
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
            path=request.path_params["path"],
            query=request.url.query.encode("utf-8")
        )

        proxy_request = media_server_proxy_client.build_request(
            request.method,
            url,
            headers=request.headers.raw,
            content=await request.body(),
            timeout=20.0,
        )

        proxy_response = await media_server_proxy_client.send(proxy_request, stream=True)

        return StreamingResponse(
            proxy_response.aiter_raw(),
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
            background=BackgroundTask(proxy_response.aclose),
        )

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

            connected_websockets[f"{client_ip}:{client_port}"] = {
                "id": str(uuid.uuid4()),
                "when_connected": time.time(),
                "websocket": websocket,
            }

            logger.info(
                f"Websocket connection accepted from {client_ip}:{client_port}"
            )
            self.sender_task = asyncio.create_task(self.sender(websocket))

            # TODO: Fix this hack which encforces streamer-system-status
            #    (ignoring system_state["media_device"]).
            await websocket.send_text(self.build_message(
                json.dumps(vibin.system_state),
                "System",
                websocket,
            ))

            # Send initial state to new client connection.
            await websocket.send_text(self.build_message(
                json.dumps(vibin.state_vars),
                "StateVars",
                websocket,
            ))

            await websocket.send_text(self.build_message(
                json.dumps(vibin.play_state),
                "PlayState",
                websocket,
            ))

            await websocket.send_text(self.build_message(
                json.dumps(vibin.streamer.transport_active_controls()),
                "ActiveTransportControls",
                websocket,
            ))

            await websocket.send_text(self.build_message(
                json.dumps(vibin.streamer.device_display),
                "DeviceDisplay",
                websocket,
            ))

            await websocket.send_text(self.build_message(
                json.dumps(vibin.favorites()), "Favorites", websocket)
            )

            await websocket.send_text(self.build_message(
                json.dumps(vibin.presets), "Presets", websocket)
            )

            await websocket.send_text(self.build_message(json.dumps(
                vibin.stored_playlist_details),
                "StoredPlaylists",
                websocket,
            ))

            await websocket.send_text(self.build_message(
                json.dumps(server_status()), "VibinStatus", websocket)
            )

            # TODO: Allow the server to send a message to all connected
            #   websockets. Perhaps just make _websocket_message_handler more
            #   publicly accessible.
            vibin._websocket_message_handler(
                "VibinStatus", json.dumps(server_status())
            )

        async def on_disconnect(
                self, websocket: WebSocket, close_code: int
        ) -> None:
            self.sender_task.cancel()
            client_ip, client_port = websocket.client

            try:
                del connected_websockets[f"{client_ip}:{client_port}"]
            except KeyError:
                pass

            vibin._websocket_message_handler(
                "VibinStatus", json.dumps(server_status())
            )

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

        def build_message(
                self, data: str, messageType: str, client_ws: WebSocket = None
        ) -> str:
            data_as_dict = json.loads(data)

            this_client = next(
                (
                    client for client in connected_websockets.values()
                    if client["websocket"] == client_ws
                ),
                None
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
                message = replace_media_server_urls_with_proxy(message, media_server_proxy_target)

            return json.dumps(message)

        async def sender(self, websocket: WebSocket) -> None:
            while True:
                to_send = await self.state_vars_queue.get()
                to_send_dict = json.loads(to_send)

                # TODO: All the json.loads()/dumps() down the path from the
                #   source through the queue and into the message builder is
                #   all a bit much -- most of it can probably be avoided.
                await websocket.send_text(self.build_message(
                    json.dumps(to_send_dict["data"]),
                    to_send_dict["type"],
                    websocket,
                ))

    # -------------------------------------------------------------------------

    logger.info(f"Starting REST API")
    logger.info(f"API docs: http://{local_ip}:{port}{vibin_app.docs_url}")

    uvicorn.run(
        vibin_app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    server_start()
