import functools
import platform
import time

from fastapi import HTTPException
import httpx

from vibin import Vibin, VibinError
from vibin.logger import logger
from vibin.models import ServerStatus
from vibin.utils import replace_media_server_urls_with_proxy

UPNP_EVENTS_BASE_ROUTE = "/upnpevents"
success = {"result": "success"}

_vibin = None
_is_proxy_for_media_server = False
_media_server_proxy_target = None
_media_server_proxy_client = None


def get_vibin_instance(
    streamer=None,
    media=None,
    discovery_timeout=5,
    subscribe_callback_base="",
    proxy_media_server=False,
) -> Vibin:
    global _vibin
    global _is_proxy_for_media_server
    global _media_server_proxy_target
    global _media_server_proxy_client

    if _vibin is not None:
        return _vibin

    logger.info("Creating Vibin instance")

    _is_proxy_for_media_server = proxy_media_server

    try:
        _vibin = Vibin(
            streamer=streamer,
            media=media,
            discovery_timeout=discovery_timeout,
            subscribe_callback_base=subscribe_callback_base,
        )

        if _vibin.media is not None:
            _media_server_proxy_target = _vibin.media.url_prefix

            if _is_proxy_for_media_server:
                _media_server_proxy_client = httpx.AsyncClient(
                    base_url=_media_server_proxy_target
                )

        return _vibin
    except VibinError as e:
        logger.error(f"Vibin server start unsuccessful: {e}")
        raise


def is_proxy_for_media_server():
    return _is_proxy_for_media_server


def get_media_server_proxy_target():
    return _media_server_proxy_target


def get_media_server_proxy_client():
    return _media_server_proxy_client


def transform_media_server_urls_if_proxying(func):
    @functools.wraps(func)
    def wrapper_transform_media_server_urls_if_proxying(*args, **kwargs):
        if _is_proxy_for_media_server:
            return replace_media_server_urls_with_proxy(
                func(*args, **kwargs), _media_server_proxy_target
            )

        return func(*args, **kwargs)

    return wrapper_transform_media_server_urls_if_proxying


def requires_media(func):
    @functools.wraps(func)
    def wrapper_requires_media(*args, **kwargs):
        if _vibin is None or _vibin.media is None:
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


def server_status() -> ServerStatus:
    global _start_time
    global _system_node
    global _system_platform
    global _system_version

    return ServerStatus(
        start_time=_start_time,
        system_node=_system_node,
        system_platform=_system_platform,
        system_version=_system_version,
        clients=[],  # WebSocket clients get injected by the WebSocket Manager
    )
