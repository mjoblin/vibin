from fastapi import APIRouter, HTTPException
import httpx
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse

from vibin.logger import logger
from vibin.server.dependencies import (
    get_media_server_proxy_client,
    is_proxy_for_media_server,
)

# -----------------------------------------------------------------------------
# The /proxy route for proxying the UPnP Media Server (e.g. album art urls).
# -----------------------------------------------------------------------------

media_server_proxy_router = APIRouter()


@media_server_proxy_router.get("/proxy/{path:path}", include_in_schema=False)
async def art_proxy(request: Request):
    if not is_proxy_for_media_server():
        raise HTTPException(
            status_code=404,
            detail="Art proxy is not enabled; see 'vibin serve --proxy-art'",
        )

    if get_media_server_proxy_client() is None:
        raise HTTPException(
            status_code=500,
            detail="Art proxy was unable to be configured",
        )

    url = httpx.URL(
        path=request.path_params["path"], query=request.url.query.encode("utf-8")
    )

    proxy_request = get_media_server_proxy_client().build_request(
        request.method,
        url,
        headers=request.headers.raw,
        content=await request.body(),
        timeout=20.0,
    )

    try:
        proxy_response = await get_media_server_proxy_client().send(
            proxy_request, stream=True
        )
    except httpx.TimeoutException:
        logger.warning(f"Proxy timed out on request: {request.url}")

        raise HTTPException(
            status_code=504,
            detail="Art proxy timed out when requesting resource",
        )

    return StreamingResponse(
        proxy_response.aiter_raw(),
        status_code=proxy_response.status_code,
        headers=proxy_response.headers,
        background=BackgroundTask(proxy_response.aclose),
    )
