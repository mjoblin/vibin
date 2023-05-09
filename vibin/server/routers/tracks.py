import json
import math

from fastapi import APIRouter, Header, HTTPException, Response

from vibin import VibinMissingDependencyError, VibinNotFoundError
from vibin.logger import logger
from vibin.models import LyricsQuery, Track
from vibin.server.dependencies import (
    get_vibin_instance,
    requires_media,
    transform_media_server_urls_if_proxying,
)

# -----------------------------------------------------------------------------
# The /tracks route.
# -----------------------------------------------------------------------------

tracks_router = APIRouter(prefix="/tracks")


@tracks_router.get("", summary="Retrieve all Track details", tags=["Tracks"])
@transform_media_server_urls_if_proxying
@requires_media
def tracks() -> list[Track]:
    try:
        return get_vibin_instance().media_server.tracks
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@tracks_router.get(
    "/{track_id}", summary="Retrieve details on a single Track", tags=["Tracks"]
)
@transform_media_server_urls_if_proxying
@requires_media
def track_by_id(track_id: str) -> Track:
    try:
        return get_vibin_instance().media_server.track(track_id)
    except VibinNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@tracks_router.get(
    "/{track_id}/lyrics", summary="Retrieve lyrics for a Track", tags=["Tracks"]
)
def track_lyrics_by_track_id(track_id: str, update_cache: bool | None = False):
    lyrics = get_vibin_instance().lyrics_for_track(
        track_id=track_id, update_cache=update_cache
    )

    if lyrics is None:
        raise HTTPException(status_code=404, detail="Lyrics not found")

    return lyrics


@tracks_router.post(
    "/{track_id}/lyrics/validate",
    summary="Mark a Track's lyrics as valid or invalid",
    tags=["Tracks"],
)
def track_lyrics_by_track_id_validate(track_id: str, is_valid: bool):
    lyrics = track_lyrics_by_track_id(track_id)
    get_vibin_instance().lyrics_valid(lyrics_id=lyrics.lyrics_id, is_valid=is_valid)

    return track_lyrics_by_track_id(track_id)


@tracks_router.get(
    "/lyrics",
    summary="Retrieve lyrics for a Track, by Artist and Title",
    description="This endpoint supports lyrics for Tracks without a local Media ID (e.g. AirPlay).",
    tags=["Tracks"],
)
def track_lyrics(artist: str, title: str, update_cache: bool | None = False):
    lyrics = get_vibin_instance().lyrics_for_track(
        artist=artist, title=title, update_cache=update_cache
    )

    if lyrics is None:
        raise HTTPException(status_code=404, detail="Lyrics not found")

    return lyrics


@tracks_router.post(
    "/lyrics/validate",
    summary="Mark lyrics as valid or invalid, by Artist and Title",
    description="This endpoint supports lyrics for Tracks without a local Media ID (e.g. AirPlay).",
    tags=["Tracks"],
)
def track_lyrics_validate(artist: str, title: str, is_valid: bool):
    lyrics = track_lyrics(artist=artist, title=title)
    get_vibin_instance().lyrics_valid(lyrics_id=lyrics["id"], is_valid=is_valid)

    return track_lyrics(artist=artist, title=title)


@tracks_router.post(
    "/lyrics/search", summary="Search all Track lyrics", tags=["Tracks"]
)
def track_lyrics_search(lyrics_query: LyricsQuery):
    results = get_vibin_instance().lyrics_search(lyrics_query.query)

    return {
        "query": lyrics_query.query,
        "matches": results,
    }


@tracks_router.get(
    "/{track_id}/links", summary="Retrieve links for a Track", tags=["Tracks"]
)
def track_links_by_track_id(track_id: str, all_types: bool = False):
    return get_vibin_instance().media_links(media_id=track_id, include_all=all_types)


@tracks_router.get(
    "/links",
    summary="Retrieve links for a Track by Artist, Album, and Title",
    tags=["Tracks"],
)
def track_links(
    artist: str | None = None,
    album: str | None = None,
    title: str | None = None,
    all_types: bool = False,
):
    return get_vibin_instance().media_links(
        artist=artist, album=album, title=title, include_all=all_types
    )


@tracks_router.get(
    "/{track_id}/waveform.png",
    summary="Retrieve a waveform image (PNG) for a Track",
    tags=["Tracks"],
)
def track_waveform_png(
    track_id: str,
    width: int = 800,
    height: int = 250,
):
    try:
        waveform = get_vibin_instance().waveform_for_track(
            track_id, data_format="png", width=width, height=height
        )

        return Response(content=waveform, media_type="image/png")
    except VibinMissingDependencyError as e:
        # TODO: Where possible, have errors reference docs for possible
        #   actions the caller can take to resolve the issue.
        logger.warning(f"Cannot generate waveform due to missing dependency: {e}")

        raise HTTPException(
            status_code=404,
            detail=f"Cannot generate waveform due to missing dependency: {e}",
        )


@tracks_router.get(
    "/{track_id}/waveform",
    summary="Retrieve waveform data (JSON) for a Track",
    tags=["Tracks"],
)
def track_waveform(
    track_id: str,
    width: int = 800,
    height: int = 250,
    accept: str | None = Header(default="application/json"),
):
    # TODO: This waveform_format / media_type / "accept" header stuff
    #   feels too convoluted.
    waveform_format = "json"
    media_type = "application/json"

    if accept == "application/octet-stream":
        waveform_format = "dat"
        media_type = "application/octet-stream"
    elif accept == "image/png":
        waveform_format = "png"
        media_type = "image/png"

    try:
        if waveform_format == "png":
            waveform = get_vibin_instance().waveform_for_track(
                track_id, data_format=waveform_format, width=width, height=height
            )
        else:
            waveform = get_vibin_instance().waveform_for_track(
                track_id, data_format=waveform_format
            )

        return Response(
            content=json.dumps(waveform) if waveform_format == "json" else waveform,
            media_type=media_type,
        )
    except VibinMissingDependencyError as e:
        # TODO: Where possible, have errors reference docs for possible
        #   actions the caller can take to resolve the issue.
        raise HTTPException(
            status_code=404,
            detail=f"Cannot generate waveform due to missing dependency: {e}",
        )


@tracks_router.get(
    "/{track_id}/rms",
    summary="Retrieve RMS (Root Mean Square) for a Track",
    tags=["Tracks"],
)
def track_rms(track_id: str):
    waveform = get_vibin_instance().waveform_for_track(track_id, data_format="json")

    samples = waveform["data"]
    squared_samples = [sample**2 for sample in samples]
    squared_sum = sum(squared_samples)

    mean = squared_sum / len(samples)
    rms = math.sqrt(mean)
    peak = max((abs(sample) for sample in samples))
    rms_to_peak_ratio = rms / peak

    return {
        "rms": rms,
        "peak": peak,
        "rms_to_peak": rms_to_peak_ratio,
    }
