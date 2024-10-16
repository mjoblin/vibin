import json
from typing import Callable
from urllib.parse import urlparse

import requests
import upnpclient
from websockets.legacy.client import WebSocketClientProtocol
from websockets.typing import Data

from vibin.amplifiers import Amplifier
from vibin.logger import logger
from vibin.models import (
    AmplifierState,
    AudioSources,
    AudioSource,
    UPnPServiceSubscriptions,
)
from vibin.types import (
    PowerState,
    MuteState,
    UpdateMessageHandler,
    UPnPProperties,
    AmplifierAction,
)
from vibin.utils import WebsocketThread


class StreamMagic(Amplifier):
    """
    Control volume via a StreamMagic streamer, such as the CXNv2.

    When a Cambridge Audio amplifier or receiver is connected via the Control
    Bus, the streamer can send signals to nudge the volume up or down. It
    can't set the volume to a specific level or report back on the current
    volume level. Additionally, the amp is automatically switched on and off
    along with the streamer.

    Alternatively, the streamer can be configured to act as a digital pre-amp,
    in which case it has full control over the volume level for the signal it
    sends to the power amp.

    If the streamer is neither configured to use the Control Bus, nor set in
    pre-amp mode, then no volume control options are available.

    See https://www.cambridgeaudio.com/row/en/blog/getting-most-your-cxn-v2-pt2-volume-control
    """

    model_name = "StreamMagic"

    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        self._device = device
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_update = on_update

        self._device_hostname = urlparse(device.location).hostname
        self._state_data = None
        self._max_volume_step = None

        self._websocket_thread = WebsocketThread(
            uri=f"ws://{self._device_hostname}:80/smoip",
            friendly_name=self._device.friendly_name,
            on_connect=self._initialize_websocket,
            on_data=self._handle_websocket_message,
            on_disconnect=self._on_disconnect,
        )
        self._websocket_thread.start()

    @property
    def name(self) -> str:
        """The UPnP device name for the Amplifier."""
        return self._device.friendly_name

    @property
    def connected(self) -> bool:
        """Whether an active connection has been established."""
        return self._websocket_thread.connected()

    @property
    def device(self) -> upnpclient.Device:
        """The UPnP device instance associated with the Amplifier."""
        return self._device

    @property
    def device_state(self) -> AmplifierState:
        """System state for the Amplifier."""
        return self._compute_amplifier_state()

    @property
    def device_udn(self) -> str:
        """The Amplifier's UPnP device UDN (Unique Device Name)."""
        return self._device.udn.removeprefix("uuid:")

    def on_startup(self) -> None:
        """Called when the Vibin system has started up."""
        pass

    def on_shutdown(self) -> None:
        """Called when the Vibin system is shut down."""
        logger.info(f"Stopping WebSocket thread for {self.name}")
        if self._websocket_thread:
            self._websocket_thread.stop()
            self._websocket_thread.join()

    # -------------------------------------------------------------------------
    # System

    @property
    def actions(self) -> list[AmplifierAction]:
        return self.device_state.actions

    @property
    def power(self) -> PowerState | None:
        """Power state."""
        return self.device_state.power

    @power.setter
    def power(self, state: PowerState) -> None:
        """Not supported.

        We could control the power state of the streamer by sending
        `power=true` or `power=false`. But that should be performed via the
        `Streamer` implementation instead.
        """
        pass

    def power_toggle(self) -> None:
        """Not supported."""
        pass

    @property
    def volume(self) -> float | None:
        """Current volume (0-1)."""
        return self.device_state.volume

    @volume.setter
    def volume(self, volume: float) -> None:
        """Set the volume (0-1)."""
        if "volume" in self.device_state.actions and self._max_volume_step:
            self._send_state_request(
                "volume_step", str(round(volume * self._max_volume_step))
            )

    def volume_up(self) -> None:
        """Increase the volume by one unit."""
        if "volume_up_down" in self.device_state.actions:
            self._send_state_request("volume_step_change", "1")

    def volume_down(self) -> None:
        """Decrease the volume by one unit."""
        if "volume_up_down" in self.device_state.actions:
            self._send_state_request("volume_step_change", "-1")

    @property
    def mute(self) -> MuteState | None:
        """Mute state."""
        return self.device_state.mute

    @mute.setter
    def mute(self, state: MuteState) -> None:
        """Set the mute state."""
        if "mute" in self.device_state.actions:
            self._send_state_request("mute", "true" if state == "on" else "false")

    def mute_toggle(self) -> None:
        """Toggle the mute state."""
        if "mute" in self.device_state.actions:
            self._send_state_request(
                "mute", "false" if self.device_state.mute == "on" else "true"
            )

    @property
    def audio_sources(self) -> AudioSources | None:
        """Not supported."""
        return self.device_state.sources

    @property
    def audio_source(self) -> AudioSource | None:
        """Not supported."""
        return None

    @audio_source.setter
    def audio_source(self, source: str) -> None:
        """Not supported."""
        pass

    # -------------------------------------------------------------------------
    # UPnP

    def subscribe_to_upnp_events(self) -> None:
        return None

    def upnp_properties(self) -> UPnPProperties:
        return {}

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return {}

    def on_upnp_event(self, service_name: str, event: str):
        return None

    # -------------------------------------------------------------------------
    # SMOIP

    async def _initialize_websocket(self, websocket: WebSocketClientProtocol):
        """On connection to the WebSocket, subscribe to StreamMagic events."""
        await websocket.send('{ "path": "/zone/state/spec", "params": { "update": 1 }}')
        await websocket.send('{ "path": "/zone/state", "params": { "update": 1 }}')
        if self._on_connect:
            self._on_connect()

    def _handle_websocket_message(self, message: Data):
        try:
            parsed = json.loads(message)
        except json.decoder.JSONDecodeError:
            return

        if "path" not in parsed:
            pass

        match parsed["path"]:
            case "/zone/state":
                self._state_data = parsed["params"]["data"]
                self._on_update("System", self.device_state)
            case "/zone/state/spec":
                try:
                    # volume_step is only present in pre-amp mode
                    self._max_volume_step = parsed["params"]["data"]["volume_step"][
                        "maximum"
                    ]
                    self._on_update("System", self.device_state)
                except KeyError:
                    pass
            case _:
                pass

    def _compute_amplifier_state(self) -> AmplifierState:
        """Converts the state messages to an AmplifierState.

        Reports the current power status, so that the Vibin UI can grey out
        controls if the streamer isn't currently available, even though this
        implementation doesn't support controlling the power.
        """
        if self._state_data and self._state_data["pre_amp_mode"]:
            return AmplifierState(
                name=self._device.friendly_name,
                actions=["volume", "mute", "volume_up_down"],
                power="on" if self._state_data["power"] else "off",
                mute="on" if self._state_data["mute"] else "off",
                volume=(
                    self._state_data["volume_step"] / self._max_volume_step
                    if self._max_volume_step
                    else None
                ),
                sources=None,
            )
        elif self._state_data and self._state_data["cbus"] in ["amplifier", "receiver"]:
            return AmplifierState(
                name=self._device.friendly_name,
                actions=["volume_up_down"],
                power="on" if self._state_data["power"] else "off",
                mute=None,
                volume=None,
                sources=None,
            )
        elif self._state_data:
            return AmplifierState(
                name=self._device.friendly_name,
                actions=[],
                power="on" if self._state_data["power"] else "off",
                mute=None,
                volume=None,
                sources=None,
            )
        else:
            return AmplifierState(
                name=self._device.friendly_name,
                actions=[],
                power=None,
                mute=None,
                volume=None,
                sources=None,
            )

    def _send_state_request(self, param: str, value: str):
        requests.get(f"http://{self._device_hostname}/smoip/zone/state?{param}={value}")
