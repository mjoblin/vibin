import queue
import re
import socket
import time
from urllib.parse import urlparse

import upnpclient

from vibin import utils, VibinDeviceError, VibinError
from vibin.amplifiers import Amplifier
from vibin.logger import logger
from vibin.models import (
    AmplifierState,
    AudioSource,
    AudioSources,
    UPnPServiceSubscriptions,
)
from vibin.types import MuteState, PowerState, UpdateMessageHandler, UPnPProperties


# -----------------------------------------------------------------------------
# Implementation of Amplifier for Hegel amplifiers.
#
# See Amplifier interface for additional method documentation.
# -----------------------------------------------------------------------------


class Hegel(Amplifier):
    model_name = "Hegel"

    def __init__(
        self,
        device: upnpclient.Device,
        upnp_subscription_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        """Implementation of the Amplifier ABC for Hegel amplifiers.

        Supports:
            * Power on/off
            * Volume control
            * Mute control
            * Input selection

        Current amplifier parameter values are tracked in local state. The
        amplifier will notify us of parameter changes over the socket; and we
        can request parameter changes over the socket. Whenever a parameter
        value changes, send a System message with the current amplifier state
        back to Vibin.

        Hegel reference documentation:
        https://support.hegel.com/component/jdownloads/send/3-files/81-h120-ip-control-codes
        """
        self._device: upnpclient.Device = device
        self._upnp_properties: UPnPProperties = {}
        self._on_update = on_update

        # Keep track of amplifier state. This will be updated as new messages
        # come in from the amplifier. This is a straight mapping of the Hegel
        # cmd/parameter pairs to a dict.
        self._state = {
            "p": None,  # power
            "i": None,  # input
            "v": None,  # volume
            "m": None,  # mute
        }

        self._cmd_queue = queue.Queue()
        self._cmd_queue_timeout = 1
        self._reset_send_time = 0

        if self._on_update:
            # Start the communication thread
            self._amp_communication_thread = utils.StoppableThread(
                target=self._handle_amp_communication
            )

            self._amp_communication_thread.start()

    @property
    def name(self) -> str:
        return self._device.friendly_name

    @property
    def device(self):
        return self._device

    @property
    def device_state(self) -> AmplifierState:
        return AmplifierState(
            name=self._device.friendly_name,
            power=self.power,
            mute=self.mute,
            volume=self.volume,
            sources=self.audio_sources,
        )

    @property
    def device_udn(self) -> str:
        return self._device.udn.removeprefix("uuid:")

    def on_shutdown(self) -> None:
        if self._amp_communication_thread:
            logger.info(f"Stopping Hegel communication thread for {self.name}")
            self._amp_communication_thread.stop()
            self._amp_communication_thread.join()

    # -------------------------------------------------------------------------
    # System
    #
    # The getters just return the current values from local amplifier state
    # (transforming the value if required; e.g. a "1" to "on"). The setters
    # send commands to the amplifier to set the new value.

    @property
    def power(self) -> PowerState:
        """Power state."""
        power_state = self._state["p"]

        if power_state == "1":
            return "on"
        else:
            return "off"

    @power.setter
    def power(self, state: PowerState) -> None:
        """Set the power state."""
        self._cmd_queue.put_nowait(("p", "1" if state == "on" else "0"))

    def power_toggle(self) -> None:
        """Toggle the power state."""
        self._cmd_queue.put_nowait(("p", "t"))

    @property
    def volume(self) -> float:
        """Current volume (0-1)."""
        try:
            vol = self._state["v"]

            if vol is None:
                return 0

            return int(vol) / 100
        except (TypeError, ValueError) as e:
            raise VibinError(f"Could determine normalized Hegel volume: {e}")

    @volume.setter
    def volume(self, volume: float) -> None:
        """Set the volume (0-1)."""
        self._cmd_queue.put_nowait(("v", str(int(volume * 100))))

    def volume_up(self) -> None:
        """Increase the volume by one unit."""
        self._cmd_queue.put_nowait(("v", "u"))

    def volume_down(self) -> None:
        """Decrease the volume by one unit."""
        self._cmd_queue.put_nowait(("v", "d"))

    @property
    def mute(self) -> MuteState:
        """Mute state."""
        mute_state = self._state["m"]

        if mute_state == "1":
            return "on"
        else:
            return "off"

    @mute.setter
    def mute(self, state: MuteState) -> None:
        """Set the mute state."""
        self._cmd_queue.put_nowait(("m", "1" if state == "on" else "0"))

    def mute_toggle(self) -> None:
        """Toggle the mute state."""
        self._cmd_queue.put_nowait(("m", "t"))

    @property
    def audio_sources(self) -> AudioSources | None:
        """Get all Audio Sources.

        Hegel only supports inputs named 1-9. Alternate/custom names, or
        additional input information, is not supported -- so the returned audio
        sources leave most AudioSource values as None.
        """
        return AudioSources(
            available=[AudioSource(id=str(num), name=str(num)) for num in range(1, 10)],
            active=self.audio_source,
        )

    @property
    def audio_source(self) -> AudioSource | None:
        """Get the active Audio Source."""
        current_source = self._state["i"]

        if current_source is None:
            return None

        return AudioSource(id=current_source, name=current_source)

    @audio_source.setter
    def audio_source(self, source: str) -> None:
        """Set the active Audio Source by name."""
        try:
            source_num = int(source)

            if source_num < 1 or source_num > 9:
                raise VibinDeviceError(f"Invalid source name (must be 1-9): {source}")
        except TypeError:
            raise VibinDeviceError(f"Invalid source name (must be 1-9): {source}")

        self._cmd_queue.put_nowait(("i", str(source_num)))

    # -------------------------------------------------------------------------
    # UPnP
    #
    # The Hegel amplifier support does not rely on UPnP (although the amplifier
    # itself does offer some UPnP services, if required in the future).

    def subscribe_to_upnp_events(self) -> None:
        pass

    @property
    def upnp_properties(self) -> UPnPProperties:
        return self._upnp_properties

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return {}

    def on_upnp_event(self, service_name: str, event: str):
        pass

    # -------------------------------------------------------------------------
    # Manage the TCP socket connection to the amplifier.
    # -------------------------------------------------------------------------

    def _handle_amp_communication(self):
        """Handle the TCP socket communication with the amplifier.

        * Connect to the amplifier and run until told to stop by vibin.
        * Read incoming messages (parse, use to update local amplifier
          state, and send an update message to notify vibin of the change).
        * Send messages upon request via the message queue.
        * Regularly send connection-drop timer requests (as described in the
          Hegel docs).
        """
        self._connect_to_amplifier()

        while True:
            # Attempt to read an incoming message from the amplifier
            try:
                response = self._socket.recv(1024)  # TODO: 1K is a lot, make smaller?
            except socket.timeout as e:
                err = e.args[0]

                if err != "timed out":
                    logger.error(f"Unexpected timeout error from {self.name}: {e}")
            except socket.error as e:
                logger.error(f"Unexpected socket error from {self.name}: {e}")
            else:
                if len(response) == 0:
                    logger.warning(f"Lost socket connection to amplifier: {self.name}")
                    self._connect_to_amplifier()

                else:
                    # We have a message from the amplifier, so use it to update
                    # local state and send a System update message
                    try:
                        command, parameter = self._process_response(response)
                        self._state[command] = parameter
                        self._on_update("System", self.device_state)
                    except VibinError as e:
                        logger.error(f"Error processing Hegel response: {e}")

            # Check if there's a message on the queue that we need to pass to
            # the amplifier; and check if we've been asked to stop processing.
            try:
                cmd = self._cmd_queue.get_nowait()
                self._send_packet(self._generate_packet(cmd[0], cmd[1]))
            except queue.Empty:
                if self._amp_communication_thread.stop_event.is_set():
                    logger.info(f"Hegel communication thread for {self.name} stopped")
                    return

            # Check if we need to send a new reset command
            if time.time() >= self._reset_send_time:
                self._send_reset_timeout()

    def _connect_to_amplifier(self):
        """Connect to the amplifier with infinite retries."""
        device_hostname = urlparse(self._device.location).hostname
        retry_interval = 5

        logger.info(f"Connecting to amplifier: {self.name}")

        while True:
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(5)
                self._socket.connect((device_hostname, 50001))

                break
            except socket.error as e:
                logger.warning(
                    f"Could not connect to socket on amplifier {self.name} "
                    + f"(retry in {retry_interval} secs): {e}"
                )
                self._socket.close()

                if self._amp_communication_thread.stop_event.is_set():
                    return

                time.sleep(retry_interval)

        logger.info(f"Connected to amplifier: {self.name}")

        self._socket.settimeout(0.5)
        self._send_reset_timeout()
        self._initialize_amp_state()

    def _initialize_amp_state(self):
        """Populate the local amplifier state with current amplifier values."""
        for command in self._state.keys():
            # These "?" commands will trigger responses from the amplifier which
            # will in turn be used to update local amplifier state.
            self._cmd_queue.put_nowait((command, "?"))

    @staticmethod
    def _generate_packet(command: str, parameter: str) -> bytes:
        """Generate a Hegel-compliant packet for the command/parameter paid.

        Packet documentation:
        https://support.hegel.com/component/jdownloads/send/3-files/81-h120-ip-control-codes
        """
        return f"-{command}.{parameter}\r".encode("utf-8")

    def _send_packet(self, packet: bytes) -> None:
        """Send a command packet to the amplifier."""
        try:
            self._socket.sendall(packet)
        except OSError:
            pass

    def _send_reset_timeout(self):
        """Send a request to the amplifier to drop our connection in the future.

        From the Hegel docs (note: we are the "controller"):

        Sending -r.3<CR> every 2 minutes, will ensure that the connection is
        reset in the event of a controller power reboot; allowing the
        controller to reconnect.
        """
        timeout_resend_mins = 2
        timeout_mins = 3

        self._send_packet(self._generate_packet("r", str(timeout_mins)))
        self._reset_send_time = time.time() + 60 * timeout_resend_mins

    @staticmethod
    def _process_response(response) -> (str, str):
        """Process a response from the amplifier.

        Returns the command/parameter-value pair as a tuple.
        """
        try:
            m = re.search(r"-(.)\.(\S+)", response.decode("utf-8"))
            result_command = m.group(1)
            result_value = m.group(2)

            if result_command == "e":
                raise VibinDeviceError(f"Got error response from Hegel: {result_value}")

            return result_command, result_value
        except (AttributeError, IndexError) as e:
            raise VibinError(f"Could not parse Hegel response: {response}")
