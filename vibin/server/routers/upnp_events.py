from fastapi import APIRouter
from starlette.requests import Request

from vibin.types import UPnPDeviceType
from vibin.server.dependencies import get_vibin_instance, UPNP_EVENTS_BASE_ROUTE

# -----------------------------------------------------------------------------
# The /upnpevents route.
#
# Forward UPnP service events to the Vibin instance for handling.
#
# The Vibin instance manages UPnP device instance (for the streamer, media
# server, and amplifier). These device instances can subscribe to UPnP events
# for zero or more UPnP services (such as AVTransport and UuVolControl)
# associated with the hardware device they talk to. Those subscriptions need to
# register an endpoint to receive update events, and this is the endpoint that
# handles those incoming events.
#
# Flow of UPnP events:
#
# UPnP event fired by device
#   -> /upnpevents/{device}/{service} (this endpoint)
#       -> vibin.on_upnp_event()
#           -> vibin.{device}.on_upnp_event()
# -----------------------------------------------------------------------------

upnp_events_router = APIRouter(prefix=UPNP_EVENTS_BASE_ROUTE, include_in_schema=False)


@upnp_events_router.api_route("/{device}/{service}", methods=["NOTIFY"])
async def upnp_event_subscription_callback(
    device: UPnPDeviceType,
    service: str,
    request: Request,
) -> None:
    body = await request.body()
    get_vibin_instance().on_upnp_event(device, service, body.decode("utf-8"))
