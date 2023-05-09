import json
from urllib.parse import urlparse

import requests
import upnpclient

from vibin import VibinError
from .logger import logger

_upnp_devices = None


def _discover_upnp_devices(timeout: int):
    """
    Perform a UPnP discovery on the local network. Found devices are cached in
    case this gets called more than once.
    """
    global _upnp_devices

    if _upnp_devices is not None:
        return _upnp_devices

    logger.info("Discovering UPnP devices...")
    devices = upnpclient.discover(timeout=timeout)

    for device in devices:
        logger.info(
            f"Found: {device.model_name} ('{device.friendly_name}') from {device.manufacturer}"
        )

    _upnp_devices = devices

    return _upnp_devices


def _determine_streamer_device(
    streamer_input: str | None, discovery_timeout: int
) -> upnpclient.Device | None:
    """
    Attempt to find a streamer on the network.

    Heuristic:

    * If the caller provides no information about the streamer then perform a
      UPnP discovery and look specifically for a MediaRenderer device from
      Cambridge Audio.
    * If a UPnP location URL is provided then attempt to use that.
    * If a hostname is provided then check to see if it's a Cambridge Audio
      device (by checking for /smoip/system/upnp).
    * Otherwise assume a UPnP friendly name was provided, in which case attempt
      to discover a UPnP device with that name.
    """
    if streamer_input is None or streamer_input == "":
        # Nothing provided by the caller, so perform a UPnP discovery and
        # attempt to find a MediaRenderer from Cambridge Audio.

        logger.info(
            "No streamer specified, attempting to auto-discover a Cambridge Audio device"
        )
        devices = _discover_upnp_devices(discovery_timeout)

        try:
            return [
                device
                for device in devices
                if device.manufacturer == "Cambridge Audio"
                and "MediaRenderer" in device.device_type
            ][0]
        except IndexError:
            raise VibinError(
                "Could not find a Cambridge Audio MediaRenderer UPnP device"
            )

    streamer_input_as_url = urlparse(streamer_input)

    if streamer_input_as_url.hostname is not None:
        # A URL was provided by the caller. Attempt to use this as the UPnP
        # device description URL.
        logger.info(
            f"Attempting to find streamer at provided UPnP location URL: {streamer_input}"
        )

        try:
            return upnpclient.Device(streamer_input)
        except requests.RequestException:
            raise VibinError(
                f"Could not find a UPnP device at the provided streamer URL: {streamer_input}"
            )
    else:
        # A non-URL was provided. This is probably either a UPnP friendly name
        # or a hostname. A hostname only works for Cambridge Audio devices.

        # See if it's a Cambridge Audio hostname.
        try:
            logger.info(
                f"Attempting to find streamer at provided hostname: {streamer_input}"
            )
            response = requests.get(
                f"http://{streamer_input}:80/smoip/system/upnp", timeout=10
            )

            if response.status_code == 200:
                try:
                    streamer = [
                        device
                        for device in response.json()["data"]["devices"]
                        if device["manufacturer"] == "Cambridge Audio"
                    ][0]

                    try:
                        return upnpclient.Device(streamer["description_url"])
                    except KeyError:
                        raise VibinError(
                            f"Cambridge Audio device found at {streamer_input}, "
                            + f"but it did not have a description_url"
                        )
                    except requests.RequestException:
                        raise VibinError(
                            f"Cambridge Audio device found at {streamer_input}, "
                            + f"but its description_url was unsuccessful: "
                            + f"{streamer['description_url']}"
                        )
                except json.decoder.JSONDecodeError:
                    # The host responded, but the response was not JSON.
                    raise VibinError(
                        f"A host was found at {streamer_input}, but it does not "
                        + f"appear to be a Cambridge Audio device."
                    )
                except KeyError:
                    # The JSON response does not include data.devices information.
                    raise VibinError(
                        f"A host was found at {streamer_input}, but it does not "
                        + f"appear to be a Cambridge Audio device."
                    )
                except IndexError:
                    raise VibinError(
                        f"Cambridge Audio device found at {streamer_input}, but "
                        + f"it did oddly not specify any devices manufactured by "
                        + f"Cambridge Audio"
                    )
        except requests.Timeout:
            raise VibinError(f"Timed out attempting to connect to {streamer_input}")
        except requests.RequestException:
            # It wasn't a Cambridge Audio host name, so see if it's one of the
            # UPnP friendly names.
            logger.info(
                f"Attempting to find streamer by UPnP friendly name: {streamer_input}"
            )
            devices = _discover_upnp_devices(discovery_timeout)

            try:
                return [
                    device
                    for device in devices
                    if device.friendly_name == streamer_input
                ][0]
            except IndexError:
                raise VibinError(
                    f"Could not find a UPnP device with friendly name '{streamer_input}'"
                )


def _determine_media_server_device(
    media_server_input: str | None,
    discovery_timeout: int,
    streamer_device: upnpclient.Device,
) -> upnpclient.Device | None:
    """
    Attempt to find a media server on the network.

    Heuristic:

    * If the caller provides no information about the media server and the
      streamer is from Cambridge Audio, then check the streamer to see if it
      knows about a UPnP device of type MediaServer. If the streamer is not
      from Cambridge Audio then perform a UPnP discovery and look for a
      MediaServer device.
    * If a UPnP location URL is provided then attempt to use that.
    * Otherwise assume a UPnP friendly name was provided, in which case attempt
      to discover a UPnP device with that name.
    """
    if media_server_input is None or media_server_input == "":
        # Nothing provided by the caller, so do one of the following:
        #   1. If the streamer_device is from Cambridge Audio then ask the
        #      streamer which MediaServer it's using.
        #   2. Auto-discover a UPnP MediaServer device.

        if streamer_device.manufacturer == "Cambridge Audio":
            logger.info(
                f"No media server specified; looking to the Cambridge Audio "
                + f"device '{streamer_device.friendly_name}' for its media server"
            )

            try:
                response = requests.get(
                    f"http://{urlparse(streamer_device.location).hostname}:80/smoip/system/upnp"
                )

                try:
                    # The Cambridge response includes a list of devices. Iterate
                    # over each of those looking for the first MediaServer.
                    media_server = [
                        cambridge_device
                        for cambridge_device in response.json()["data"]["devices"]
                        if "MediaServer"
                        in upnpclient.Device(
                            cambridge_device["description_url"]
                        ).device_type
                    ][0]

                    return upnpclient.Device(media_server["description_url"])
                except IndexError:
                    logger.watning(
                        f"Cambridge Audio device '{streamer_device.friendly_name}' "
                        + f"did not specify a media server device"
                    )
                    return None
            except (
                requests.RequestException,
                json.decoder.JSONDecodeError,
                KeyError,
                IndexError,
            ) as e:
                raise VibinError(
                    f"Could not determine media server from Cambridge Audio device: {e}"
                )
        else:
            # Auto-discover a MediaServer device.
            logger.info("No media server specified, attempting auto-discovery")
            devices = _discover_upnp_devices(discovery_timeout)

            try:
                return [
                    device for device in devices if "MediaServer" in device.device_type
                ][0]
            except IndexError:
                logger.warning("Could not find a MediaServer UPnP device")
                return None

    media_input_as_url = urlparse(media_server_input)

    if media_input_as_url.hostname is not None:
        # Check UPnP location url
        logger.info(
            f"Attempting to find media server at provided URL: {media_server_input}"
        )

        try:
            return upnpclient.Device(media_server_input)
        except requests.RequestException:
            raise VibinError(
                f"Could not find a UPnP device at the provided media server URL: {media_server_input}"
            )
    else:
        # Check UPnP friendly name option
        logger.info(
            f"Attempting to find media server by UPnP friendly name: {media_server_input}"
        )
        devices = _discover_upnp_devices(discovery_timeout)

        try:
            return [
                device
                for device in devices
                if device.friendly_name == media_server_input
            ][0]
        except IndexError:
            raise VibinError(
                f"Could not find a UPnP device with friendly name '{media_server_input}'"
            )


def determine_streamer_and_media_server(
    streamer_input: str | None,
    media_server_input: str | bool | None,
    discovery_timeout: int = 5,
) -> (upnpclient.Device, upnpclient.Device | None):
    """
    Attempt to locate a streamer and (optionally) a media server on the network.
    """
    streamer_device = _determine_streamer_device(streamer_input, discovery_timeout)

    media_server_device = None

    if media_server_input is not False:
        media_server_device = _determine_media_server_device(
            None if media_server_input is True else media_server_input,
            discovery_timeout,
            streamer_device,
        )

    return streamer_device, media_server_device
