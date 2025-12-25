"""Device resolution for Vibin.

This module provides functions to discover UPnP devices and determine
which Vibin implementation classes to use for them.
"""

import asyncio
import inspect
import json
from urllib.parse import urlparse

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client_factory import UpnpFactory
import requests

from vibin import VibinError
from vibin.amplifiers import model_to_amplifier, Amplifier
import vibin.amplifiers as amplifiers
from vibin.logger import logger
import vibin.mediaservers as mediaservers
from vibin.mediaservers import MediaServer, model_to_media_server
import vibin.streamers as streamers
from vibin.streamers import model_to_streamer, Streamer
from vibin.upnp import (
    VibinDevice,
    async_discover_devices,
    wrap_device,
)

_upnp_devices: list[VibinDevice] | None = None


# =============================================================================
# UPnP device discovery (sync wrappers around async functions)
# =============================================================================


def _run_async(coro):
    """Run an async coroutine synchronously.

    Creates a new event loop if needed. This is used to provide backward
    compatibility for sync code that needs to use async discovery.
    """
    try:
        asyncio.get_running_loop()
        # We're already in an async context, can't use asyncio.run()
        # This shouldn't happen in normal Vibin usage, but handle it gracefully
        raise RuntimeError(
            "Cannot use sync device discovery from within an async context."
        )
    except RuntimeError as e:
        if "no running event loop" in str(e):
            # No running loop, we can create one
            return asyncio.run(coro)
        raise


async def _async_discover_all_devices(timeout: int) -> list[VibinDevice]:
    """Async discovery of all UPnP devices."""
    return await async_discover_devices(timeout=timeout)


def _discover_upnp_devices(timeout: int) -> list[VibinDevice]:
    """Perform a UPnP discovery of all devices on the local network.

    Found devices are cached in case this gets called more than once. This
    discovers all devices regardless of type.
    """
    global _upnp_devices

    if _upnp_devices is not None:
        return _upnp_devices

    logger.info("Discovering UPnP devices...")
    devices = _run_async(_async_discover_all_devices(timeout))

    for device in devices:
        logger.info(
            f"Found: {device.model_name} ('{device.friendly_name}') from {device.manufacturer}"
        )

    _upnp_devices = devices

    return _upnp_devices


async def _async_create_device_from_url(url: str) -> VibinDevice:
    """Create a device from a UPnP description URL.

    Creates a fresh factory and session for each call to avoid issues
    with event loop lifecycle in sync contexts.
    """
    async with aiohttp.ClientSession() as session:
        requester = AiohttpSessionRequester(session, with_sleep=True)
        factory = UpnpFactory(requester, non_strict=True)
        device = await factory.async_create_device(url)
        return wrap_device(device)


def _create_device_from_url(url: str) -> VibinDevice:
    """Sync wrapper to create a device from a UPnP description URL."""
    return _run_async(_async_create_device_from_url(url))


def _determine_streamer_device(
    streamer_input: str | None, discovery_timeout: int
) -> VibinDevice | None:
    """Attempt to find a streamer on the network.

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
            return _create_device_from_url(streamer_input)
        except Exception:
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
                        return _create_device_from_url(streamer["description_url"])
                    except KeyError:
                        raise VibinError(
                            f"Cambridge Audio device found at {streamer_input}, "
                            + f"but it did not have a description_url"
                        )
                    except Exception:
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
    streamer_device: VibinDevice,
) -> VibinDevice | None:
    """Attempt to find a media server on the network.

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
                    devices_data = response.json()["data"]["devices"]
                    logger.info(
                        f"Cambridge Audio device reported {len(devices_data)} UPnP devices"
                    )

                    for cambridge_device in devices_data:
                        description_url = cambridge_device.get("description_url")

                        if not description_url:
                            continue

                        try:
                            dev = _create_device_from_url(description_url)
                            logger.info(
                                f"Checking device: {dev.model_name} "
                                f"({dev.device_type}) at {description_url}"
                            )

                            if "MediaServer" in dev.device_type:
                                return dev
                        except Exception as e:
                            logger.warning(
                                f"Failed to create device from {description_url}: {e}"
                            )
                            continue

                    logger.warning(
                        f"Cambridge Audio device '{streamer_device.friendly_name}' "
                        + f"did not specify a media server device"
                    )

                    return None
                except IndexError:
                    logger.warning(
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
            return _create_device_from_url(media_server_input)
        except Exception:
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


def _determine_amplifier_device(
    amplifier_input: str | None,
    discovery_timeout: int,
    streamer_device: VibinDevice,
) -> VibinDevice | None:
    """Attempt to find an amplifier on the network.

    Heuristic:

    * If the caller provides no information about the amplifier then perform a
      UPnP discovery and look specifically for a MediaRenderer device. If more
      than one MediaRenderer is discovered, return the first which is not the
      same as the streamer.
    * If a UPnP location URL is provided then attempt to use that.
    * If a UPnP friendly name was provided, attempt to discover a UPnP device
      with that name.
    """
    if amplifier_input is None or amplifier_input == "":
        # Nothing provided by the caller, so perform a UPnP discovery and
        # attempt to find a MediaRenderer/amplifier.

        logger.info(
            "No amplifier specified, attempting to auto-discover a UPnP MediaRenderer"
        )
        devices = _discover_upnp_devices(discovery_timeout)

        try:
            media_renderers = [
                device
                for device in devices
                if "MediaRenderer" in device.device_type
            ]

            if len(media_renderers) == 1:
                # This allows for the streamer device to also be the amplifier
                # device if there's only one MediaRenderer.
                return media_renderers[0]
            else:
                return [
                    device
                    for device in media_renderers
                    if device.udn != streamer_device.udn
                ][0]
        except IndexError:
            # No MediaRenderers is not an error state for amplifiers (amplifiers
            # are optional for Vibin).
            return None

    amplifier_input_as_url = urlparse(amplifier_input)

    if amplifier_input_as_url.hostname is not None:
        # Check UPnP location url
        logger.info(
            f"Attempting to find amplifier at provided URL: {amplifier_input}"
        )

        try:
            return _create_device_from_url(amplifier_input)
        except Exception:
            raise VibinError(
                f"Could not find a UPnP device at the provided amplifier URL: {amplifier_input}"
            )
    else:
        # Check UPnP friendly name option
        logger.info(
            f"Attempting to find amplifier by UPnP friendly name: {amplifier_input}"
        )
        devices = _discover_upnp_devices(discovery_timeout)

        try:
            return [
                device
                for device in devices
                if device.friendly_name == amplifier_input
            ][0]
        except IndexError:
            raise VibinError(
                f"Could not find a UPnP device with friendly name '{amplifier_input}'"
            )


def determine_devices(
    streamer_input: str | None,
    media_server_input: str | bool | None,
    amplifier_input: str | bool | None,
    discovery_timeout: int = 5,
) -> tuple[VibinDevice, VibinDevice | None, VibinDevice | None]:
    """Attempt to locate a streamer and (optionally) a media server on the network."""

    streamer_device = _determine_streamer_device(streamer_input, discovery_timeout)

    media_server_device = None

    if media_server_input is not False:
        media_server_device = _determine_media_server_device(
            None if media_server_input is True else media_server_input,
            discovery_timeout,
            streamer_device,
        )

    amplifier_device = None

    if amplifier_input is not False:
        amplifier_device = _determine_amplifier_device(
            None if amplifier_input is True else amplifier_input,
            discovery_timeout,
            streamer_device,
        )

    return streamer_device, media_server_device, amplifier_device


def determine_streamer_class(streamer_device: VibinDevice, streamer_type: str | None):
    """Determine which Streamer implementation matches the streamer_device."""

    # Build a list of all known Streamer implementations; and a map of device
    # model name to Streamer implementation.
    known_streamers = []
    known_streamers_by_model: dict[str, Streamer] = {}

    for name, obj in inspect.getmembers(streamers):
        if inspect.isclass(obj) and issubclass(obj, Streamer):
            known_streamers.append(obj)
            known_streamers_by_model[obj.model_name] = obj

    # Inject any model additions/overrides
    known_streamers_by_model.update(model_to_streamer)

    # Determine which Streamer implementation to use
    streamer_class = None

    if streamer_type is None:
        # Match the device's model name against the known models, ignoring
        # case and whitespace in an attempt to be reasonably flexible
        model_fuzzy = streamer_device.model_name.lower().replace(" ", "")

        for (known_model, klass) in known_streamers_by_model.items():
            if known_model.lower().replace(" ", "") == model_fuzzy:
                streamer_class = klass
                break

        if streamer_class is None:
            raise VibinError(
                f"Could not find Vibin implementation for streamer model "
                + f"'{streamer_device.model_name}'"
            )
    else:
        # A specific Streamer implementation was requested.
        streamer_class = next(
            (
                streamer for streamer in known_streamers
                if streamer.__name__ == streamer_type
            ),
            None
        )

        if streamer_class is None:
            raise VibinError(
                f"Could not find Vibin implementation for requested "
                + f"streamer type: {streamer_type}"
            )

    return streamer_class


def determine_media_server_class(
    media_server_device: VibinDevice, media_server_type: str | None
):
    """Determine which MediaServer implementation matches the media_server_device."""

    # Build a list of all known MediaServer implementations; and a map of
    # device model name to MediaServer implementation.
    known_media_servers = []
    known_media_servers_by_model: dict[str, MediaServer] = {}

    for name, obj in inspect.getmembers(mediaservers):
        if inspect.isclass(obj) and issubclass(obj, MediaServer):
            known_media_servers.append(obj)
            known_media_servers_by_model[obj.model_name] = obj

    # Inject any additions/overrides
    known_media_servers_by_model.update(model_to_media_server)

    # Determine which MediaServer implementation to use
    try:
        if media_server_type is None:
            media_server_class = known_media_servers_by_model[
                media_server_device.model_name
            ]
        else:
            # A specific MediaServer implementation was requested.
            media_server_class = next(
                (
                    media_server for media_server in known_media_servers
                    if media_server.__name__ == media_server_type
                ),
                None
            )

            if media_server_class is None:
                raise VibinError(
                    f"Could not find Vibin implementation for requested "
                    + f"media server type: {media_server_type}"
                )
    except KeyError:
        raise VibinError(
            f"Could not find Vibin implementation for media server model "
            + f"'{media_server_device.model_name}'"
        )

    return media_server_class


def determine_amplifier_class(
    amplifier_device: VibinDevice, amplifier_type: str | None
):
    """Determine which Amplifier implementation matches the amplifier_device."""

    # Build a list of all known Amplifier implementations; and a map of
    # device model name to Amplifier implementation.
    known_amplifiers = []
    known_amplifiers_by_model: dict[str, Amplifier] = {}

    for name, obj in inspect.getmembers(amplifiers):
        if inspect.isclass(obj) and issubclass(obj, Amplifier):
            known_amplifiers.append(obj)
            known_amplifiers_by_model[obj.model_name] = obj

    # Inject any additions/overrides
    known_amplifiers_by_model.update(model_to_amplifier)

    # Determine which Amplifier implementation to use
    try:
        if amplifier_type is None:
            amplifier_class = known_amplifiers_by_model[
                amplifier_device.model_name
            ]
        else:
            # A specific Amplifier implementation was requested.
            amplifier_class = next(
                (
                    amplifier for amplifier in known_amplifiers
                    if amplifier.__name__ == amplifier_type
                ),
                None
            )

            if amplifier_class is None:
                raise VibinError(
                    f"Could not find Vibin implementation for requested "
                    + f"amplifier type: {amplifier_type}"
                )
    except KeyError:
        raise VibinError(
            f"Could not find Vibin implementation for amplifier model "
            + f"'{amplifier_device.model_name}'"
        )

    return amplifier_class
