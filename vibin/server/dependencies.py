import functools
import platform
import time

from fastapi import HTTPException
import httpx

from vibin import Vibin, VibinError
from vibin.constants import VIBIN_VER
from vibin.logger import logger
from vibin.models import VibinStatus, WebSocketClientDetails
from vibin.utils import replace_media_server_urls_with_proxy

UPNP_EVENTS_BASE_ROUTE = "/upnpevents"

_vibin: Vibin | None = None
_is_proxy_for_media_server = False
_media_server_proxy_target = None
_media_server_proxy_client = None
_ui_static_root = None


# NOTE: The vibin server relies on singleton instance of the Vibin class. This
#   instance is retrieved by any REST endpoint which needs to communicate with
#   the Vibin backend. The singleton instance is first created when the API
#   is initialized (see server.py), before the API is ready to receive incoming
#   requests.

def get_vibin_instance(
    streamer=None,
    streamer_type=None,
    media_server=None,
    media_server_type=None,
    discovery_timeout=5,
    upnp_subscription_callback_base="",
    proxy_media_server=False,
    ui_static_root=None,
) -> Vibin:
    """Return the Vibin singleton instance."""
    global _vibin
    global _is_proxy_for_media_server
    global _media_server_proxy_target
    global _media_server_proxy_client
    global _ui_static_root

    if _vibin is not None:
        return _vibin

    logger.info("Creating Vibin instance")

    _is_proxy_for_media_server = proxy_media_server
    _ui_static_root = ui_static_root

    try:
        _vibin = Vibin(
            streamer=streamer,
            streamer_type=streamer_type,
            media_server=media_server,
            media_server_type=media_server_type,
            discovery_timeout=discovery_timeout,
            upnp_subscription_callback_base=upnp_subscription_callback_base,
        )

        if _vibin.media_server is not None:
            _media_server_proxy_target = _vibin.media_server.url_prefix

            if _is_proxy_for_media_server:
                _media_server_proxy_client = httpx.AsyncClient(
                    base_url=_media_server_proxy_target
                )

    except VibinError as e:
        logger.error(f"Vibin server start unsuccessful: {e}")
        raise

    if _is_proxy_for_media_server:
        # If proxying the media server has been requested but there's no media
        # server associated with the vibin instance, then do not proceed.
        if _vibin.media_server is not None:
            logger.info(
                f"Proxying art at /proxy (target: {get_media_server_proxy_target()})"
            )
        else:
            error = "Unable to proxy art; media server not located"
            logger.error(error)
            _vibin.shutdown()

            raise VibinError(error)

    return _vibin


def is_proxy_for_media_server():
    """Is the vibin server acting as a proxy for the media server.

    Clients accessing the vibin server will usually be on the same network as
    the vibin server, and will also be able to directly access the media server
    (say to retrieve album art). Sometimes however a client won't be able to
    access the media server directly, in which case the vibin server can act as
    a proxy. See the --proxy-media-server flag for "vibin serve".
    """
    return _is_proxy_for_media_server


def get_media_server_proxy_target():
    return _media_server_proxy_target


def get_media_server_proxy_client():
    return _media_server_proxy_client


def get_ui_static_root():
    return _ui_static_root


def transform_media_server_urls_if_proxying(func):
    """Decorator to transform any media server URLs to point to the proxy.

    This decorator is intended to be attached to functions which are returning
    an arbitrary payload to the calling client. If the vibin server is proxying
    the media server, then the payload has its URLs transformed to point to the
    proxy.
    """
    @functools.wraps(func)
    def wrapper_transform_media_server_urls_if_proxying(*args, **kwargs):
        if _is_proxy_for_media_server:
            return replace_media_server_urls_with_proxy(
                func(*args, **kwargs), _media_server_proxy_target
            )

        return func(*args, **kwargs)

    return wrapper_transform_media_server_urls_if_proxying


def requires_media(func):
    """Decorator to return a 404 if the vibin server does not have a media server."""
    @functools.wraps(func)
    def wrapper_requires_media(*args, **kwargs):
        if _vibin is None or _vibin.media_server is None:
            raise HTTPException(
                status_code=404,
                detail="Feature unavailable (no local media server registered with Vibin)",
            )

        return func(*args, **kwargs)

    return wrapper_requires_media


_start_time = time.time()
_system_node = platform.node()
_system_platform = platform.platform()
_system_version = platform.version()


def server_status(
    websocket_clients: list[WebSocketClientDetails] | None = None,
) -> VibinStatus:
    """Return a VibinStatus instance describing the vibin server."""

    global _start_time
    global _system_node
    global _system_platform
    global _system_version

    return VibinStatus(
        vibin_version=VIBIN_VER,
        start_time=_start_time,
        system_node=_system_node,
        system_platform=_system_platform,
        system_version=_system_version,
        clients=websocket_clients if websocket_clients is not None else [],
    )
