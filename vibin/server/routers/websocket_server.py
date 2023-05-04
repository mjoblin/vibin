import asyncio
import json
import time
from typing import Any
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from vibin import VibinError
from vibin.logger import logger
from vibin.models import (
    ServerStatus,
    UpdateMessage,
    UpdateMessageType,
    WebSocketClientDetails,
)
from vibin.server.dependencies import (
    get_media_server_proxy_target,
    get_vibin_instance,
    is_proxy_for_media_server,
    server_status,
)
from vibin.utils import replace_media_server_urls_with_proxy

# https://github.com/tiangolo/fastapi/issues/81


# -----------------------------------------------------------------------------
# The /ws WebSocket server route.
# -----------------------------------------------------------------------------

websocket_server_router = APIRouter()


class ConnectionManager:
    """
    Manage all WebSocket client connections.

    This entails:
        * Maintaining a list of active connections.
        * Managing a message queue to receive all updates from Vibin.
        * Sending all the Vibin update messages to all connected clients.
    """

    def __init__(self):
        self.active_connections = {}
        self.message_queue = asyncio.Queue()
        self.sender_task = None
        self.registered_listener_with_vibin = False

    async def connect(self, websocket: WebSocket):
        """Handle a new client connection."""
        await websocket.accept()

        if not self.registered_listener_with_vibin:
            # Register the WebSocket server's message handler with the main
            # Vibin instance if that hasn't been done already. This should only
            # happen once upon the first client connection.
            #
            # This isn't done in the ConnectionManager's __init__() as overall
            # system startup hasn't progressed far enough at that point; but
            # once the first client connection is received, everything is
            # ready for this step to take place.

            # TODO: Clean up using "state_vars_handler" for both state_vars
            #   (UPnP) updates and websocket updates.
            vibin = get_vibin_instance()
            vibin.on_state_vars_update(self.state_vars_handler)
            vibin.on_websocket_update(self.websocket_update_handler)

            self.sender_task = asyncio.create_task(self.auto_broadcast())
            self.registered_listener_with_vibin = True

        # Add the new connection's details to the list of active connections.
        self.active_connections[websocket] = {
            "id": str(uuid.uuid4()),
            "when_connected": time.time(),
            "websocket": websocket,
        }

    def disconnect(self, websocket: WebSocket):
        """Handle a client disconnect."""
        del self.active_connections[websocket]

    def websocket_update_handler(self, message_type: UpdateMessageType, data: Any):
        """
        Receive all WebSocket update messages from Vibin.

        All Vibin update messages are put on a queue for later sending to all
        connected clients.
        """
        # TODO: Don't override state_vars queue for both state vars and websocket updates.
        self.message_queue.put_nowait(
            item=UpdateMessage(message_type=message_type, message=data)
        )

    def state_vars_handler(self, data: str):
        """
        Receive all StateVars update messages from Vibin.

        TODO: Merge this with websocket_update_handler(), *if* the old
            StateVars concept remains after a future message-type refactor.
        """
        self.message_queue.put_nowait(
            item=UpdateMessage(message_type="StateVars", message=data)
        )

    def message_payload_to_str(self, message_payload: Any):
        """
        Convert a message payload of any type to a string.

        The goal is to be as flexible as possible, allowing the message
        producers to pass any payload (a pydantic BaseModel, a dict, a string,
        or anything else). If it can be converted to a string then it can be
        sent on to each client.
        """
        if isinstance(message_payload, str):
            return message_payload
        elif isinstance(message_payload, BaseModel):
            return json.dumps(message_payload.dict())
        else:
            try:
                return json.dumps(message_payload)
            except TypeError:
                pass

        raise VibinError(
            f"Could not convert message of type '{type(message_payload)}' to string"
        )

    def build_message(
        self,
        messageType: UpdateMessageType,
        message_payload_str: str,
        client_ws: WebSocket = None,
    ) -> str:
        """
        Construct a WebSocket message to send to a single client.

        Each message contains:
            * id: A unique ID, specific to the message.
            * client_id: The ID of the client the message is being sent to.
            * time: A timestamp for when the message was created.
            * type: The message type.
            * payload: The message payload.

        Will update any payload URLs to point to the proxy if required.

        Note: Each message contains a client_id, which is why each built
            message is client-specific -- requiring client_ws be passed.
        """
        # Create a message shell. The payload will be updated later.
        message = {
            "id": str(uuid.uuid4()),
            "client_id": self.active_connections[client_ws]["id"],
            "time": int(time.time() * 1000),
            "type": messageType,
            "payload": None,
        }

        if messageType == "System":
            # TODO: Fix this hack. We're assuming we're getting a streamer
            #   system update, but it might be a media_source update.
            # message["payload"] = {
            #     "streamer": message_payload_parsed,
            # }
            #
            # TODO UPDATE: We now ignore the incoming data and just emit a
            #   full system_state payload.
            message["payload"] = get_vibin_instance().system_state
        else:
            try:
                message["payload"] = json.loads(message_payload_str)
            except TypeError:
                # Getting here is unexpected. If for some reason the
                # message_str is not JSON-friendly then we just pass it on as
                # raw text.
                logger.warning(
                    f"Message could not be parsed as JSON; sending as plain "
                    + f"text: {message_payload_str}"
                )
                message["payload"] = message_payload_str

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
        """
        Send any new message on the message queue to all connected clients.

        This is effectively an automatic broadcast of all messages reveived on
        the queue to all connected clients.
        """
        while True:
            to_send: UpdateMessage = await self.message_queue.get()

            try:
                message_payload_str = self.message_payload_to_str(to_send.payload)
            except VibinError as e:
                logger.warning(f"Could not send message over Websocket: {e}")
                return

            for client_websocket in self.active_connections.keys():
                await client_websocket.send_text(
                    self.build_message(
                        to_send.message_type,
                        message_payload_str,
                        client_websocket,
                    )
                )

    async def single_client_send(
        self,
        websocket: WebSocket,
        message_type: UpdateMessageType,
        message_payload: Any,
    ) -> None:
        """Send a message to a single client."""
        try:
            message_str = self.message_payload_to_str(message_payload)
        except VibinError as e:
            logger.warning(f"Could not send message over Websocket: {e}")
            return

        await websocket.send_text(
            self.build_message(message_type, message_str, websocket)
        )

    def client_details(self) -> list[WebSocketClientDetails]:
        """Return information on each of the currently-connected clients."""
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

    def get_status(self) -> ServerStatus:
        """Return the current Vibin server status."""
        return server_status(websocket_clients=self.client_details())

    def shutdown(self):
        """Handle a shutdown request of the WebSocket server."""
        self.sender_task.cancel()


# -----------------------------------------------------------------------------

ws_connection_manager = ConnectionManager()


@websocket_server_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Accept a new WebSocket client connection.

    For each new connection:

        * Send a batch of messages describing the current system state.
        * Notify all other clients of this new client connection.
        * Block until the client disconnects.
        * On disconnect, notify all other clients of the disconnection.

    NOTE: An instance of this handler remains active for each client
        connection. The instance will only complete once WebSocketDisconnect is
        raised when the client disconnects.
    """
    await ws_connection_manager.connect(websocket)

    client_ip, client_port = websocket.client
    logger.info(f"WebSocket connection accepted from {client_ip}:{client_port}")

    vibin = get_vibin_instance()

    # Send all current state messages to the new connection. The goal here is
    # to give the client all the information it needs up-front to understand
    # the complete state of the system.
    for state_message in vibin.get_current_state_messages():
        await ws_connection_manager.single_client_send(
            websocket, state_message.message_type, state_message.payload
        )

    # Send the current Vibin server status to the new connection *and* to all
    # other existing connections (so they're aware of this new connection).
    vibin_status = ws_connection_manager.get_status()

    await ws_connection_manager.single_client_send(
        websocket, "VibinStatus", vibin_status
    )
    ws_connection_manager.websocket_update_handler("VibinStatus", vibin_status)

    try:
        while True:
            # We don't expect to ever receive anything from the client, but if
            # we do then we log it and otherwise ignore it. The main goal here
            # is to block until the client disconnects.
            data = await websocket.receive_text()
            client_ip, client_port = websocket.client
            logger.warning(
                f"Got unexpected message from client WebSocket [{client_ip}:{client_port}]: {data}"
            )
    except WebSocketDisconnect:
        ws_connection_manager.disconnect(websocket)

        client_ip, client_port = websocket.client

        # Send a VibinStatus message to all other clients, so they're aware
        # that one of the other clients (the one in this scope) has
        # disconnected.
        vibin_status = ws_connection_manager.get_status()
        ws_connection_manager.websocket_update_handler("VibinStatus", vibin_status)

        logger.info(f"WebSocket connection closed for client {client_ip}:{client_port}")
