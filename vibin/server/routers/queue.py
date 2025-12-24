from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from vibin import VibinNotFoundError
from vibin.models import Queue, QueueModifyPayload
from vibin.types import PlaylistModifyAction
from vibin.server.dependencies import (
    get_vibin_instance,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /queue route.
# -----------------------------------------------------------------------------

queue_router = APIRouter(prefix="/queue")


@queue_router.get(
    "",
    summary="Retrieve details on the Streamer's Queue",
    tags=["Queue"],
    response_model_by_alias=False,
)
@transform_media_server_urls_if_proxying
def queue() -> Queue:
    return get_vibin_instance().streamer.queue


@queue_router.post(
    "/play/id/{item_id}",
    summary="Play a Queue Item, by Item ID",
    tags=["Queue"],
    response_class=Response,
)
def queue_play_item_id(item_id: int):
    get_vibin_instance().streamer.play_queue_item_id(item_id)


@queue_router.post(
    "/play/position/{item_position}",
    summary="Play a Queue Item, by Item position",
    tags=["Queue"],
    response_class=Response,
)
def queue_play_item_position(item_position: int):
    try:
        get_vibin_instance().streamer.play_queue_item_position(item_position)
    except VibinNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Queue item not found at position: {item_position}",
        )


@queue_router.post(
    "/play/favorites/albums",
    summary="Replace the Queue with Album favorites",
    tags=["Queue"],
    response_class=Response,
)
def queue_play_favorite_albums(max_count: int = 10):
    get_vibin_instance().play_favorite_albums(max_count=max_count)


@queue_router.post(
    "/play/favorites/tracks",
    summary="Replace the Queue with Track favorites",
    tags=["Queue"],
    response_class=Response,
)
def queue_play_favorite_tracks(max_count: int = 100):
    get_vibin_instance().play_favorite_tracks(max_count=max_count)


@queue_router.post(
    "/modify",
    summary="Modify the Queue with multiple Media IDs",
    description=(
        "Currently, the only supported action is `REPLACE`, which replaces the "
        + "Streamer's active Queue with the provided Media IDs."
    ),
    tags=["Queue"],
    response_class=Response,
)
def queue_modify_multiple_items(payload: QueueModifyPayload):
    if payload.action != "REPLACE":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action: {payload.action}. Supported actions: REPLACE.",
        )

    get_vibin_instance().play_ids(payload.media_ids, max_count=payload.max_count)


@queue_router.post(
    "/modify/{media_id}",
    summary="Modify the Queue with a single Media ID",
    tags=["Queue"],
    response_class=Response,
)
def queue_modify_single_item(
    media_id: str,
    action: PlaylistModifyAction = "REPLACE",
    play_from_id: str | None = None,
):
    get_vibin_instance().playlists_manager.modify_streamer_queue_with_id(
        media_id, action, play_from_id
    )


@queue_router.post(
    "/move/{item_id}",
    summary="Move a Queue Item to a different position",
    tags=["Queue"],
    response_class=Response,
)
def queue_move_item(item_id: int, from_position: int, to_position: int):
    get_vibin_instance().streamer.queue_move_item(item_id, from_position, to_position)


@queue_router.post(
    "/clear",
    summary="Clear the Queue",
    tags=["Queue"],
    response_class=Response,
)
def queue_clear():
    get_vibin_instance().streamer.queue_clear()


@queue_router.post(
    "/delete/{item_id}",
    summary="Remove a Queue Item",
    tags=["Queue"],
    response_class=Response,
)
def queue_delete_item(item_id: int):
    get_vibin_instance().streamer.queue_delete_item(item_id)
