import concurrent.futures

from tinydb import Query
from tinyrecord import transaction
import xml
import xmltodict

from vibin.external_services import ExternalService
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.models import ExternalServiceLink, Links
from vibin.types import MediaId


class LinksManager:
    """Links manager.

    Manages the generation of links associated with the provided
    external_services. Links are stored in the provided db.

    Example links: Wikipedia artist page, album page, track page; Genius
    lyrics link; etc.

    The provided db is expected to be a TinyDB table.
    """

    def __init__(
        self,
        db,
        media_server: MediaServer,
        external_services: dict[str, ExternalService],
    ):
        self._db = db
        self._media_server = media_server
        self._external_services = external_services

    def media_links(
        self,
        *,
        media_id: MediaId | None = None,
        artist: str | None = None,
        album: str | None = None,
        title: str | None = None,
        include_all: bool = False,
    ) -> dict[ExternalService.name, list[ExternalServiceLink]]:
        """Return links to each external service for the given media_id or
        artist/album/title.

        Pass a media_id for a track on local storage. Where the media id is
        unknown (for a non-local audio source like AirPlay), pass the
        artist/album/track details.
        """

        if len(self._external_services) == 0:
            return {}

        # Check if links are already stored
        if media_id:
            StoredLinksQuery = Query()
            stored_links = self._db.get(StoredLinksQuery.media_id == media_id)

            if stored_links is not None:
                links_data = Links(**stored_links)
                return links_data.links

        results = {}

        # TODO: Have errors raise an exception which can be passed back to the
        #   caller, rather than empty {} results.

        if media_id:
            try:
                media_info = xmltodict.parse(self._media_server.get_metadata(media_id))
                didl = media_info["DIDL-Lite"]

                if "container" in didl:
                    # Album
                    artist = didl["container"]["dc:creator"]
                    album = didl["container"]["dc:title"]
                elif "item" in didl:
                    # Track
                    artist = self._artist_name_from_track_media_info(media_info)
                    album = didl["item"]["upnp:album"]
                    title = didl["item"]["dc:title"]
                else:
                    logger.error(
                        f"Could not determine whether media item is an Album or "
                        + f"a Track: {media_id}"
                    )
                    return {}
            except xml.parsers.expat.ExpatError as e:
                logger.error(
                    f"Could not convert XML to JSON for media item: {media_id}: {e}"
                )
                return {}
            except KeyError as e:
                logger.error(f"Could not find expected media key in {media_id}: {e}")
                return {}

        try:
            link_type = "All" if include_all else ("Album" if not title else "Track")

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_link_getters = {
                    executor.submit(
                        service.links,
                        **{
                            "artist": artist,
                            "album": album,
                            "track": title,
                            "link_type": link_type,
                        },
                    ): service
                    for service in self._external_services.values()
                }

                for future in concurrent.futures.as_completed(future_to_link_getters):
                    link_getter = future_to_link_getters[future]

                    try:
                        results[link_getter.name] = future.result()
                    except Exception as exc:
                        logger.error(
                            f"Could not retrieve links from "
                            + f"{link_getter.name}: {exc}"
                        )
        except xml.parsers.expat.ExpatError as e:
            logger.error(
                f"Could not convert XML to JSON for media item: {media_id}: {e}"
            )

        if media_id:
            # Persist to local data store.
            link_data = Links(
                media_id=media_id,
                links=results,
            )

            with transaction(self._db) as tr:
                tr.insert(link_data.dict())

        return results

    @staticmethod
    def _artist_name_from_track_media_info(track_info) -> str | None:
        """Attempt to extract the artist name from the given track details."""
        artist = None

        # TODO: Centralize all the DIDL-parsing logic. It might be helpful to have
        #   one centralized way to provide some XML media info and extract all the
        #   useful information from it, in a Vibin-contract-friendly way (well-
        #   defined concepts for title, artist, album, track artist vs. album
        #   artist, composer, etc).

        try:
            didl_item = track_info["DIDL-Lite"]["item"]

            # Default to dc:creator
            artist = didl_item["dc:creator"]

            # Attempt to find AlbumArtist in upnp:artist
            upnp_artist_info = didl_item["upnp:artist"]

            if type(upnp_artist_info) == str:
                artist = upnp_artist_info
            else:
                # We have an array of artists, so look for AlbumArtist (others
                # might be Composer, etc).
                for upnp_artist in upnp_artist_info:
                    if upnp_artist["@role"] == "AlbumArtist":
                        artist = upnp_artist["#text"]
                        break
        except KeyError:
            pass

        return artist
