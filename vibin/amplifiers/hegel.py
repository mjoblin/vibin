from dataclasses import dataclass
import queue
import re
import socket
import time
from typing import Callable, Literal
from urllib.parse import urlparse

from vibin import utils, VibinDeviceError, VibinError
from vibin.amplifiers import Amplifier
from vibin.logger import logger
from vibin.models import (
    AmplifierState,
    AudioSource,
    AudioSources,
    UPnPServiceSubscriptions,
)
from vibin.types import (
    AmplifierAction,
    MuteState,
    PowerState,
    UpdateMessageHandler,
    UPnPProperties,
)
from vibin.upnp import VibinDevice

# -----------------------------------------------------------------------------
# Implementation of Amplifier for Hegel amplifiers.
#
# See Amplifier interface for additional method documentation.
# -----------------------------------------------------------------------------

HegelCommandName = Literal["e", "i", "m", "p", "r", "v"]


@dataclass
class HegelCommand:
    name: HegelCommandName
    parameter: str


class Hegel(Amplifier):
    model_name = "Hegel"

    def __init__(
        self,
        device: VibinDevice,
        upnp_subscription_callback_base: str | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
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
        self._device: VibinDevice = device
        self._upnp_properties: UPnPProperties = {}
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_update = on_update

        self._source_names = {
            1: "Balanced",
            2: "Analog 1",
            3: "Analog 2",
            4: "Coaxial",
            5: "Optical 1",
            6: "Optical 2",
            7: "Optical 3",
            8: "USB",
            9: "Network",
        }

        # Keep track of amplifier state. This will be updated as new messages
        # come in from the amplifier. This is a straight mapping of the Hegel
        # cmd/parameter pairs to a dict.
        self._state: dict[HegelCommandName, str | None] = {
            "p": None,  # power
            "i": None,  # input
            "v": None,  # volume
            "m": None,  # mute
        }

        self._cmd_queue: queue.Queue[HegelCommand] = queue.Queue()
        self._cmd_queue_timeout = 1
        self._reset_send_time = 0
        self._socket = None
        self._connected = False
        self._last_sent_command: HegelCommand | None = None

        # Start the heartbeat and communication threads
        self._amp_heartbeat_thread = utils.StoppableThread(target=self._amp_heartbeat)
        self._amp_heartbeat_thread.start()

        self._amp_communication_thread = utils.StoppableThread(
            target=self._handle_amp_communication
        )

        self._amp_communication_thread.start()

    @property
    def name(self) -> str:
        return self._device.friendly_name

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device(self):
        return self._device

    @property
    def device_state(self) -> AmplifierState:
        return AmplifierState(
            name=self._device.friendly_name,
            supported_actions=self.supported_actions,
            power=self.power,
            mute=self.mute,
            max_volume=self.max_volume,
            volume=self.volume,
            sources=self.audio_sources,
        )

    @property
    def device_udn(self) -> str:
        return self._device.udn.removeprefix("uuid:")

    def on_startup(self) -> None:
        pass

    def on_shutdown(self) -> None:
        if self._amp_heartbeat_thread:
            logger.info(f"Stopping Hegel heartbeat thread for {self.name}")
            self._amp_heartbeat_thread.stop()
            self._amp_heartbeat_thread.join()

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
    def supported_actions(self) -> list[AmplifierAction]:
        return ["power", "volume", "mute", "volume_up_down", "audio_source"]

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
        self._cmd_queue.put_nowait(
            HegelCommand(name="p", parameter="1" if state == "on" else "0")
        )

    def power_toggle(self) -> None:
        """Toggle the power state."""
        self._cmd_queue.put_nowait(HegelCommand(name="p", parameter="t"))

    @property
    def max_volume(self) -> int | None:
        """Maximum volume level."""
        return 100

    @property
    def volume(self) -> int:
        """Current volume (zero to max_volume)."""
        try:
            vol = self._state["v"]

            if vol is None:
                return 0

            return int(vol)
        except (TypeError, ValueError) as e:
            raise VibinError(f"Could determine normalized Hegel volume: {e}")

    @volume.setter
    def volume(self, volume: int) -> None:
        """Set the volume (zero to max_volume)."""
        self._cmd_queue.put_nowait(HegelCommand(name="v", parameter=str(volume)))

    def volume_up(self) -> None:
        """Increase the volume by one unit."""
        self._cmd_queue.put_nowait(HegelCommand(name="v", parameter="u"))

    def volume_down(self) -> None:
        """Decrease the volume by one unit."""
        self._cmd_queue.put_nowait(HegelCommand(name="v", parameter="d"))

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
        self._cmd_queue.put_nowait(
            HegelCommand(name="m", parameter="1" if state == "on" else "0")
        )

    def mute_toggle(self) -> None:
        """Toggle the mute state."""
        self._cmd_queue.put_nowait(HegelCommand(name="m", parameter="t"))

    @property
    def audio_sources(self) -> AudioSources | None:
        """Get all Audio Sources.

        Hegel only supports inputs named 1-9. Alternate/custom names, or
        additional input information, is not supported -- so the returned audio
        sources leave most AudioSource values as None.
        """
        return AudioSources(
            available=[
                AudioSource(id=str(num), name=self._source_name_by_id(num))
                for num in range(1, 10)
            ],
            active=self.audio_source,
        )

    @property
    def audio_source(self) -> AudioSource | None:
        """Get the active Audio Source."""
        current_source = self._state["i"]

        if current_source is None:
            return None

        return AudioSource(
            id=current_source, name=self._source_name_by_id(int(current_source))
        )

    @audio_source.setter
    def audio_source(self, source: str) -> None:
        """Set the active Audio Source by name."""
        try:
            source_num = self._source_id_by_name(source)

            if source_num is None:
                raise VibinDeviceError(f"Invalid source name: {source}")
        except TypeError:
            raise VibinDeviceError(f"Invalid source name (must be 1-9): {source}")

        self._cmd_queue.put_nowait(HegelCommand(name="i", parameter=str(source_num)))

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
    # Helpers
    def _source_name_by_id(self, source_id: int) -> str | None:
        try:
            return self._source_names[source_id]
        except IndexError:
            return None

    def _source_id_by_name(self, source_name: str) -> int | None:
        for this_id, this_name in self._source_names.items():
            if this_name == source_name:
                return this_id

        return None

    def _request_command_send(self, command: HegelCommand):
        """Request the send of a HegelCommand to the amplifier.

        Does not repeat the same command more than once in a sequence."""
        if command != self._last_sent_command:
            self._cmd_queue.put_nowait(command)

    # -------------------------------------------------------------------------
    # Manage the TCP socket connection to the amplifier.

    def _amp_heartbeat(self):
        """Maintain an amplifier heartbeat.

        Sends a power state request to the amplifier every 3 seconds. This acts
        as a form of heartbeat, allowing for the detection of a loss of
        connection to the amplifier.
        """
        while True:
            if self._amp_heartbeat_thread.stop_event.is_set():
                logger.info(f"Hegel heartbeat thread for {self.name} stopped")
                return

            if self._connected:
                # Bypass self._request_command_send() as we want to force this
                #   command onto the queue rather than having it potentially be
                #   filtered out as a repeat.
                self._cmd_queue.put_nowait(HegelCommand(name="p", parameter="?"))
                time.sleep(3)

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

        expecting_response = False

        while True:
            # Attempt to read an incoming message from the amplifier
            try:
                # TODO: This implementation assumes we get a single complete
                #   message per call to recv(). To be more robust, it should
                #   allow for receiving more than one message -- and receiving
                #   partial messages. Messages appear to be \r delimited.
                #
                # TODO: 1K is a lot to recv, make a smaller power of 2? 8 might
                #   be enough given the compactness of the protocol.
                #
                # TODO: This approach relies on the amplifier responding to a
                #   sent command within the socket timeout window. If no
                #   response is received in time then the socket is closed.
                #   This seems to be OK with a 500ms timeout but this logic
                #   could be tightened up to allow for a less responsive
                #   amplifier.
                response = self._socket.recv(1024)
            except socket.timeout as e:
                if expecting_response:
                    logger.warning(
                        f"Did not receive response from {self.name} within the "
                        + "expected timeout window; reconnecting"
                    )
                    self._connect_to_amplifier()
                elif e == "timed out":
                    logger.error(
                        f"Unexpected socket timeout error while reading from {self.name}: {e}"
                    )
                    self._connect_to_amplifier()
            except ConnectionResetError as e:
                logger.info(
                    f"Amplifier {self.name} has reset the connection; attempting reconnect"
                )
                self._connect_to_amplifier(resend_last_packet=True)
            except socket.error as e:
                logger.error(f"Unexpected socket error from {self.name}: {e}")
                self._connect_to_amplifier()
            else:
                if len(response) == 0:
                    logger.warning(f"Lost socket connection to amplifier: {self.name}")
                    self._connect_to_amplifier()
                else:
                    # We have a message from the amplifier, so use it to update
                    # local state and send a System update message

                    expecting_response = False

                    try:
                        command = self._process_response(response)

                        old_parameter_value = self._state[command.name]
                        self._state[command.name] = command.parameter

                        if self._on_update and self._state[command.name] != old_parameter_value:
                            self._on_update("System", self.device_state)
                    except VibinError as e:
                        logger.error(f"Error processing Hegel response: {e}")

            # Check if there's a message on the queue that we need to pass to
            # the amplifier; and check if we've been asked to stop processing.
            try:
                command = self._cmd_queue.get_nowait()

                expecting_response = True
                self._send_packet(self._generate_packet(command))
            except queue.Empty:
                if self._amp_communication_thread.stop_event.is_set():
                    logger.info(f"Hegel communication thread for {self.name} stopped")
                    return

            # Check if we need to send a new reset command
            if time.time() >= self._reset_send_time:
                self._send_reset_timeout()

    def _connect_to_amplifier(self, resend_last_packet=False):
        """Connect to the amplifier with infinite retries."""
        device_hostname = urlparse(self._device.location).hostname

        if self._socket is not None:
            logger.info("Attempting to close amplifier socket before reconnecting")
            try:
                self._socket.close()
                self._socket = None
                self._handle_disconnect()
            except Exception as e:
                logger.info(f"Error closing amplifier socket: {e}")

        logger.info(f"Connecting to amplifier: {self.name}")

        retry_interval = 5  # How long to wait before retrying if connect failed

        while True:
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(3)  # Wait for 3 seconds when connecting

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

        self._socket.settimeout(0.5)  # Subsequent socket read timeout
        self._send_reset_timeout()
        self._initialize_amp_state()

        if resend_last_packet and self._last_sent_command is not None:
            logger.info(
                f"Re-sending last-attempted packet to amplifier: {self._last_sent_command}"
            )
            self._send_packet(self._generate_packet(self._last_sent_command))

        self._connected = True

        if self._on_connect:
            self._on_connect()

    def _handle_disconnect(self):
        """Handle a connection loss."""
        self._connected = False

        if self._on_disconnect:
            self._on_disconnect()

    def _initialize_amp_state(self):
        """Populate the local amplifier state with current amplifier values."""
        for command_name in self._state.keys():
            # These "?" commands will trigger responses from the amplifier which
            # will in turn be used to update local amplifier state.
            self._cmd_queue.put_nowait(HegelCommand(name=command_name, parameter="?"))

    def _generate_packet(self, command: HegelCommand) -> bytes:
        """Generate a Hegel-compliant socket packet from the given HegelCommand.

        Packet documentation:
        https://support.hegel.com/component/jdownloads/send/3-files/81-h120-ip-control-codes
        """
        self._last_sent_command = command
        packet = f"-{command.name}.{command.parameter}\r".encode("utf-8")

        return packet

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
        # NOTE: This approach seems to cause issues with the amplifier, where
        #   an attempt to reconnect after an extended period causes the service
        #   listening on 50001 to exit/crash, resulting in nothing but
        #   "connection refused" errors. Disabling for now.
        return

        timeout_resend_mins = 2
        timeout_mins = 3

        logger.info(f"Sending reset duration: {timeout_mins}")
        self._send_packet(
            self._generate_packet(HegelCommand(name="r", parameter=str(timeout_mins)))
        )
        self._reset_send_time = time.time() + 60 * timeout_resend_mins

    @staticmethod
    def _process_response(response) -> HegelCommand:
        """Process a response from the amplifier.

        Returns the command/parameter-value pair as a tuple.
        """
        try:
            m = re.search(r"-(.)\.(\S+)", response.decode("utf-8"))
            result_command_name = m.group(1)
            result_parameter_value = m.group(2)

            if result_command_name == "e":
                raise VibinDeviceError(
                    f"Got error response from Hegel: {result_parameter_value}"
                )

            return HegelCommand(name=result_command_name, parameter=result_parameter_value)
        except (AttributeError, IndexError) as e:
            raise VibinError(f"Could not parse Hegel response: {response}")
