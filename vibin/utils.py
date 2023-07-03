from collections.abc import Iterable
import dataclasses
from distutils.version import StrictVersion
import functools
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import tempfile
import threading
import time
import zipfile

from pydantic import BaseModel
import requests
import upnpclient

from vibin import VibinError, VibinMissingDependencyError
from vibin.constants import UI_APPNAME, UI_BUILD_DIR, UI_REPOSITORY, UI_ROOT
from vibin.logger import logger
from vibin.models import UPnPServiceSubscriptions, UPnPSubscription

ONE_HOUR_IN_SECS = 60 * 60
ONE_MIN_IN_SECS = 60
HMMSS_MATCH = re.compile("^\d+:\d{2}:\d{2}(\.\d+)?$")


# =============================================================================
# General utilities
# =============================================================================

# -----------------------------------------------------------------------------
# Classes

class StoppableThread(threading.Thread):
    """A Thread class which allows for external stopping.

    The thread can be stopped externally by calling thread.stop(). Requires the
    thread target to frequently check whether stop_event is set.
    """
    def __init__(self, *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def stopped(self):
        return self.stop_event.is_set()


class UPnPSubscriptionManagerThread(StoppableThread):
    def __init__(
        self,
        device: upnpclient.Device,
        cmd_queue: queue.Queue,
        subscription_callback_base: str,
        services: list[upnpclient.Service],
        *args,
        **kwargs,
    ):
        """Thread to manage UPnP subscription initialization and renewals.

        Initializes UPnP subscriptions to all provided UPnP services, and
        instructs them to send events to the subscription_callback_base URL.
        Handles subscription renewal on timeout.

        Valid commands on cmd_queue:

            * "SUBSCRIBE": Initialize the UPnP subscriptions.
            * "SHUTDOWN": Stop managing UPnP subscriptions and shut down.

        Managed UPnP service subscriptions are exposed on the subscriptions
        property.

        Note: This class does not act on incoming UPnP events. It merely handles
        the UPnP subscriptions to ensure that they are initialized and renewed
        when necessary. Incoming UPnP events (resulting from the subscriptions)
        are expected to be handled elsewhere.
        """
        super().__init__(*args, **kwargs)

        self.name = "UPnP-SubManagerThread"

        self._device = device
        self._cmd_queue = cmd_queue
        self._services = services
        self._subscription_callback_base = subscription_callback_base

        self._subscriptions: UPnPServiceSubscriptions = {}
        self._cmd_queue_timeout = 1
        self._device_name = self._device.friendly_name

    def run(self):
        while True:
            try:
                cmd = self._cmd_queue.get(timeout=self._cmd_queue_timeout)

                if cmd == "SUBSCRIBE":
                    self.subscribe_to_upnp_events()
                elif cmd == "SHUTDOWN":
                    logger.info(
                        f"UPnP subscription manager thread for {self._device_name} "
                        + "shutting down"
                    )
                    self.cancel_subscriptions()
                    logger.info(
                        f"UPnP subscription manager thread for {self._device_name} ended"
                    )
                    return
            except queue.Empty:
                if self.stop_event.is_set():
                    logger.info(
                        f"UPnP subscription manager thread for {self._device_name} ended"
                    )
                    return

                # Check if any subscriptions have timed out and need renewal
                self.renew_subscriptions_if_required()

    @property
    def subscriptions(self) -> UPnPServiceSubscriptions:
        """All managed UPnP subscriptions."""
        return self._subscriptions

    def subscribe_to_upnp_events(self) -> None:
        """Subscribe to UPnP events for all provided UPnP services."""
        for service in self._services:
            now = int(time.time())

            try:
                (subscription_id, timeout) = service.subscribe(
                    callback_url=f"{self._subscription_callback_base}/{service.name}"
                )
            except requests.RequestException as e:
                logger.warning(
                    f"Could not subscribe to {self._device_name} UPnP events "
                    + f"for {service.name}: {e}"
                )
                return

            self._subscriptions[service] = UPnPSubscription(
                id=subscription_id,
                timeout=timeout,
                next_renewal=(now + timeout) if timeout else None,
            )

            logger.info(
                f"Subscribed to {self._device_name} UPnP events from {service.name} "
                + f"with timeout {timeout}s"
            )

    def renew_subscriptions_if_required(self):
        """Renew the subscriptions to the streamer's UPnP services.

        Subscriptions need to be renewed after their timeout has expired.
        """
        renewal_buffer = 10
        renew_retry_delay = 10

        for service, subscription in self._subscriptions.items():
            now = int(time.time())

            if (subscription.timeout is not None) and (
                now > (subscription.next_renewal - renewal_buffer)
            ):
                logger.info(
                    f"Renewing {self._device_name} UPnP subscription for {service.name}"
                )

                try:
                    timeout = service.renew_subscription(subscription.id)
                    subscription.timeout = timeout
                    subscription.next_renewal = (now + timeout) if timeout else None
                except requests.RequestException:
                    logger.warning(
                        f"Could not renew {self._device_name} UPnP subscription for "
                        + f"{service.name}. Will attempt a cancel and re-subscribe "
                        + f"of all subscriptions in {renew_retry_delay} seconds."
                    )

                    time.sleep(renew_retry_delay)
                    self.cancel_subscriptions()
                    self.subscribe_to_upnp_events()

    def cancel_subscriptions(self):
        """Cancel all subscriptions to the streamer's UPnP services.

        Subscription cancellation is treated as non-essential (the assumption is
        that the streamer will clean up old/unused subscriptions). The attempt
        to unsubscribe is made, but problems are ignored aside from some logging
        to announce the issue.
        """
        for service, subscription in self._subscriptions.items():
            try:
                logger.info(
                    f"Canceling {self._device_name} UPnP subscription for {service.name}"
                )
                service.cancel_subscription(subscription.id)
            except (upnpclient.UPNPError, upnpclient.soap.SOAPError) as e:
                logger.warning(
                    f"Could not cancel {self._device_name} UPnP subscription for "
                    + f"{service.name}: {e}"
                )
            except requests.RequestException as e:
                fail_message = (
                    f"Could not cancel {self._device_name} UPnP subscription for "
                    + f"{service.name} [{e.response.status_code}]"
                )

                if e.response.status_code == 412:
                    fail_message += " (subscription appears to have expired)"

                logger.warning(f"fail_message: {e}")

        self._subscriptions = {}


# -----------------------------------------------------------------------------
# Functions

def get_local_ip() -> str:
    """Determine the IP address of the local host."""

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


def is_hmmss(input: str) -> bool:
    """True if the given input string matches "h:mm:ss(.ms)"."""
    return bool(HMMSS_MATCH.match(input))


def secs_to_hmmss(input_secs: int) -> str:
    """Converts the given number of input seconds to "h:mm:ss"."""
    hours = math.floor(input_secs / ONE_HOUR_IN_SECS)
    mins = math.floor((input_secs - hours * ONE_HOUR_IN_SECS) / ONE_MIN_IN_SECS)
    secs = input_secs - (hours * ONE_HOUR_IN_SECS) - (mins * ONE_MIN_IN_SECS)

    return f"{hours}:{mins:02}:{secs:02}"


def hmmss_to_secs(input_hmmss: str) -> int:
    """Converts the given "h:mm:ss" string to a number of whole seconds."""
    if not is_hmmss(input_hmmss):
        raise TypeError("Time must be in h:mm:ss format")

    [h, mm, ss] = [float(component) for component in input_hmmss.split(":")]

    return round(h * ONE_HOUR_IN_SECS + mm * ONE_MIN_IN_SECS + ss)


def replace_media_server_urls_with_proxy(payload, media_server_url_prefix):
    """Replace all media server URLs in the payload with a proxy URL.

    The given payload is expected to be a REST response payload or WebSocket
    message being sent to a client (e.g. the UI). The payload is checked to see
    if it includes anything that looks ike a URL referring to the media server.
    Matching URLs are then modified to instead point to the proxy. If the client
    later accesses the proxied URL then the proxy will retrieve the data from
    the media server and send it back to the client.
    """
    def transform(item):
        item_is_iterable = isinstance(item, Iterable)

        if isinstance(item, BaseModel) or dataclasses.is_dataclass(item):
            # The item is a data class or a pydantic model

            uri_attrs = ["album_art_uri", "albumArtURI", "uri", "art_url"]

            # TODO: Extend this case to work like iterables, which support
            #   transforming nested fields as well as transforming any
            #   string value which starts with the proxy target (rather
            #   than just a hardcoded list of attr names like is done here)

            for uri_attr in uri_attrs:
                if hasattr(item, uri_attr):
                    setattr(
                        item,
                        uri_attr,
                        getattr(item, uri_attr).replace(
                            media_server_url_prefix, "/proxy"
                        ),
                    )
        elif item_is_iterable:
            # The item is a dict, list, or string.
            if isinstance(item, dict):
                # If the item is a dict
                for key, value in item.items():
                    if isinstance(value, str) and value.startswith(
                        media_server_url_prefix
                    ):
                        item[key] = value.replace(media_server_url_prefix, "/proxy")
                    elif isinstance(value, Iterable):
                        item[key] = transform(value)
            elif isinstance(item, list):
                # If the item is a list
                return [transform(child) for child in item]
            else:
                # Probably a string
                return item

        return item

    return transform(payload)


# -----------------------------------------------------------------------------
# Decorators

def requires_media_server(return_val=None):
    """Decorator to only allow the call if the object has a media server reference.

    This decorator assumes it is being used on a method of a class. The class
    instance is checked to ensure that it has a self.media_server or
    self._media_server before the call is allowed to proceed.

    If a media server is not available then return_val is returned as the result
    of the call.
    """
    def decorator_requires_media_server(func):
        @functools.wraps(func)
        def wrapper_requires_media_server(self, *args, **kwargs):
            if (
                hasattr(self, "media_server") and self.media_server is not None
            ) or (
                hasattr(self, "_media_server") and self._media_server is not None
            ):
                return func(self, *args, **kwargs)
            else:
                return return_val

        return wrapper_requires_media_server

    return decorator_requires_media_server


def requires_external_service_token(func):
    """Decorator to return VibinMissingDependencyError if the service has no token.

    This decorator assumes it is being used on a method of a class, where the
    class instance defines self._external_service (an ExternalService instance).
    """

    @functools.wraps(func)
    def wrapper_requires_external_service_token(self, *args, **kwargs):
        if (
            self._external_service is not None
            and self._external_service.token is not None
        ):
            return func(self, *args, **kwargs)
        else:
            raise VibinMissingDependencyError("External service token")

    return wrapper_requires_external_service_token


# -----------------------------------------------------------------------------
# UI installation

def install_vibinui():
    """Install the Vibin Web UI's static files.

    This installs the static files of the Web UI locally so they can be served
    by the vibin backend. Installing the UI involves:

        * Determine the latest UI release version from GitHub.
        * Create a directory to store the files.
        * Download the UI archive from GitHub.
        * Extract the build/ directory (i.e. static files) from the archive.
    """
    logger.info(f"Installing the Web UI into: {UI_ROOT}")

    # Create the UI root directory if it doesn't already exist
    os.makedirs(UI_ROOT, exist_ok=True)

    # Call the GitHub API to get the tag name for "latest"
    try:
        logger.info(
            f"Retrieving latest version tag from GitHub repository ({UI_REPOSITORY})..."
        )
        response = requests.get(
            f"https://api.github.com/repos/{UI_REPOSITORY}/releases/latest"
        )
        api_response = response.json()

        latest_tag = api_response["tag_name"]
        logger.info(f"Installing version {latest_tag}")
    except (requests.RequestException, json.JSONDecodeError, KeyError):
        raise VibinError("Could not determine latest UI release tag from GitHub")

    # Download and extract the files.
    try:
        # Build the path to the latest release archive zipfile
        latest_zip = f"https://github.com/{UI_REPOSITORY}/archive/{latest_tag}.zip"
        logger.info(f"Downloading {latest_tag} archive from GitHub...")

        with tempfile.TemporaryFile() as local_ui_zipfile:
            # Download the latest release archive zipfile
            with requests.get(latest_zip, stream=True) as response:
                shutil.copyfileobj(response.raw, local_ui_zipfile)

            # Extract the build directory from the zipfile to the requested location
            with zipfile.ZipFile(local_ui_zipfile, "r") as zip_data:
                logger.info(f"Unpacking files...")

                top_level_zip_dir = zip_data.filelist[0].filename
                ui_install_dir = Path(UI_ROOT, top_level_zip_dir)

                if ui_install_dir.is_dir():
                    raise VibinError(
                        f"Install directory already exists: {ui_install_dir}"
                    )

                ui_build_files = [
                    file for file in zip_data.namelist() if UI_BUILD_DIR in file
                ]

                if len(ui_build_files) <= 0:
                    raise VibinError(
                        f"Web UI archive does not contain any '{UI_BUILD_DIR}' files"
                    )

                for ui_build_file in ui_build_files:
                    zip_data.extract(ui_build_file, path=UI_ROOT)

            logger.info(f"Web UI {latest_tag} installed into: {ui_install_dir}")
            logger.info(
                f"Web UI {latest_tag} will be served automatically with 'vibin serve'; "
                + "or override with '--vibinui'"
            )
    except requests.RequestException as e:
        raise VibinError(
            f"Could not download the {latest_tag} release from GitHub: {e}"
        )
    except zipfile.BadZipFile:
        raise VibinError(
            f"The downloaded UI archive does not appear to be a valid zipfile"
        )


def get_ui_install_dir() -> Path | None:
    """Determine which local directory to install the UI.

    Directory will be under the project root, named something like
    _webui/vibinui-1.0.0/.
    """
    try:
        candidates = [
            uidir
            for uidir in os.listdir(UI_ROOT)
            if uidir.startswith(UI_APPNAME) and os.path.isdir(Path(UI_ROOT, uidir))
        ]
    except FileNotFoundError:
        return None

    candidate_versions = [
        candidate.replace(f"{UI_APPNAME}-", "") for candidate in candidates
    ]

    candidate_versions.sort(key=StrictVersion, reverse=True)

    try:
        return Path(UI_ROOT, f"{UI_APPNAME}-{candidate_versions[0]}")
    except IndexError:
        return None
