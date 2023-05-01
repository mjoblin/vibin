import asyncio
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from vibin.logger import logger
from vibin.models import WebSocketClientDetails
from vibin.server.dependencies import (
    get_media_server_proxy_target,
    get_vibin_instance,
    is_proxy_for_media_server,
    server_status,
)
from vibin.utils import replace_media_server_urls_with_proxy

# https://github.com/tiangolo/fastapi/issues/81


websocket_server_router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        self.state_vars_queue = asyncio.Queue()
        self.sender_task = None
        self.registered_listener_with_vibin = False

    async def connect(self, websocket: WebSocket):
        await websocket.accept()

        if not self.registered_listener_with_vibin:
            # TODO: Clean up using "state_vars_handler" for both state_vars
            #   (UPnP) updates and websocket updates.
            vibin = get_vibin_instance()
            vibin.on_state_vars_update(self.state_vars_handler)
            vibin.on_websocket_update(self.websocket_update_handler)

            self.sender_task = asyncio.create_task(self.auto_broadcast())
            self.registered_listener_with_vibin = True

        self.active_connections[websocket] = {
            "id": str(uuid.uuid4()),
            "when_connected": time.time(),
            "websocket": websocket,
        }

    def disconnect(self, websocket: WebSocket):
        del self.active_connections[websocket]

    def state_vars_handler(self, data: str):
        self.state_vars_queue.put_nowait(
            item=json.dumps({"type": "StateVars", "data": json.loads(data)})
        )

    def websocket_update_handler(self, message_type: str, data: str):
        # TODO: Don't override state_vars queue for both state vars and
        #   websocket updates.
        self.state_vars_queue.put_nowait(
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

        message = {
            "id": str(uuid.uuid4()),
            "client_id": self.active_connections[client_ws]["id"],
            "time": int(time.time() * 1000),
            "type": messageType,
        }

        # TODO: This (the streamer- and media-server-agnostic layer)
        #   shouldn't have any awareness of the CXNv2 data shapes. So the
        #   ["data"]["params"] stuff below should be abstracted away.

        vibin = get_vibin_instance()

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
        if is_proxy_for_media_server() and messageType in [
            "DeviceDisplay",
            "Favorites",
            "PlayState",
            "Presets",
            "StateVars",
        ]:
            message = replace_media_server_urls_with_proxy(
                message, get_media_server_proxy_target()
            )

        return json.dumps(message)

    async def auto_broadcast(self) -> None:
        while True:
            # TODO: All the json.loads()/dumps() down the path from the
            #   source through the queue and into the message builder is
            #   all a bit much -- most of it can probably be avoided.

            to_send = await self.state_vars_queue.get()
            to_send_dict = json.loads(to_send)
            message_text = json.dumps(to_send_dict["data"])

            for client_websocket in self.active_connections.keys():
                await client_websocket.send_text(
                    self.build_message(
                        message_text,
                        to_send_dict["type"],
                        client_websocket,
                    )
                )

    async def single_client_send(
        self, websocket: WebSocket, type: str, message: str
    ) -> None:
        await websocket.send_text(self.build_message(message, type, websocket))

    def client_details(self) -> list[WebSocketClientDetails]:
        clients: list[WebSocketClientDetails] = []

        for websocket_info in self.active_connections.values():
            client_ip, client_port = websocket_info["websocket"].client

            clients.append(
                WebSocketClientDetails(
                    id=websocket_info["id"],
                    when_connected=websocket_info["when_connected"],
                    ip=client_ip,
                    port=client_port,
                )
            )

        return clients

    def get_status(self):
        return server_status(websocket_clients=self.client_details())

    def shutdown(self):
        self.sender_task.cancel()


websocket_connection_manager = ConnectionManager()


@websocket_server_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_connection_manager.connect(websocket)

    client_ip, client_port = websocket.client
    logger.info(f"WebSocket connection accepted from {client_ip}:{client_port}")

    vibin = get_vibin_instance()

    # TODO: Fix this hack which encforces streamer-system-status
    #    (ignoring system_state["media_device"]).
    await websocket_connection_manager.single_client_send(
        websocket, "System", json.dumps(vibin.system_state)
    )

    # Send initial state to new client connection.
    await websocket_connection_manager.single_client_send(
        websocket, "StateVars", json.dumps(vibin.state_vars)
    )

    await websocket_connection_manager.single_client_send(
        websocket, "PlayState", json.dumps(vibin.play_state)
    )

    await websocket_connection_manager.single_client_send(
        websocket,
        "ActiveTransportControls",
        json.dumps(vibin.streamer.transport_active_controls()),
    )

    await websocket_connection_manager.single_client_send(
        websocket, "DeviceDisplay", json.dumps(vibin.streamer.device_display)
    )

    await websocket_connection_manager.single_client_send(
        websocket, "Favorites", json.dumps(vibin.favorites())
    )

    await websocket_connection_manager.single_client_send(
        websocket, "Presets", json.dumps(vibin.presets)
    )

    await websocket_connection_manager.single_client_send(
        websocket, "StoredPlaylists", json.dumps(vibin.stored_playlist_details)
    )

    await websocket_connection_manager.single_client_send(
        websocket,
        "VibinStatus",
        json.dumps(websocket_connection_manager.get_status().dict()),
    )

    # TODO: Allow the server to send a message to all connected
    #   websockets. Perhaps just make _websocket_message_handler more
    #   publicly accessible.
    vibin._websocket_message_handler(
        "VibinStatus", json.dumps(websocket_connection_manager.get_status().dict())
    )

    try:
        while True:
            data = await websocket.receive_text()
            client_ip, client_port = websocket.client
            logger.warning(
                f"Got unexpected message from client WebSocket [{client_ip}:{client_port}]: {data}"
            )
    except WebSocketDisconnect:
        websocket_connection_manager.disconnect(websocket)

        client_ip, client_port = websocket.client

        # Send a VibinStatus message to all other clients, so they're aware
        # than one of the other clients has disconnected.
        vibin = get_vibin_instance()
        vibin._websocket_message_handler(
            "VibinStatus", json.dumps(websocket_connection_manager.get_status().dict())
        )

        logger.info(f"WebSocket connection closed for client {client_ip}:{client_port}")
