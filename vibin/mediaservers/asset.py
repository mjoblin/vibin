import asyncio
from functools import lru_cache
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse
import xml
import xml.etree.ElementTree as ET

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.exceptions import (
    UpnpActionError,
    UpnpActionResponseError,
    UpnpConnectionError,
    UpnpConnectionTimeoutError,
)
import untangle
import xmltodict

from vibin import VibinNotFoundError, VibinSoapError
from vibin.exceptions import VibinMediaServerError
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.models import (
    Album,
    Artist,
    MediaBrowseSingleLevel,
    MediaFolder,
    MediaServerState,
    Track,
    UPnPServiceSubscriptions,
)
from vibin.types import MediaId, MediaType, UpdateMessageHandler, UPnPProperties
from vibin.upnp import VibinDevice
from vibin.utils import run_coroutine_sync


# -----------------------------------------------------------------------------
# Implementation of MediaServer for the Asset UPnP Server.
#
# See MediaServer interface for method documentation.
#
# https://dbpoweramp.com/asset-upnp-dlna.htm
# -----------------------------------------------------------------------------


class Asset(MediaServer):
    model_name = "Asset UPnP Server"

    def __init__(
        self,
        device: VibinDevice,
        upnp_subscription_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        self._device: VibinDevice = device

        self._all_albums_path: str | None = None
        self._new_albums_path: str | None = None
        self._all_artists_path: str | None = None

        self._albums_by_id: dict[MediaId, Album] = {}
        self._artists_by_id: dict[MediaId, Artist] = {}
        self._tracks_by_id: dict[MediaId, Track] = {}

        self._upnp_properties: UPnPProperties = {}

        # Request tracking for debugging
        self._inflight_requests = 0
        self._inflight_lock = threading.Lock()

        # Limit concurrent requests to prevent overwhelming Asset UPnP server
        # (4 concurrent requests was observed to cause 17+ second timeouts)
        self._request_semaphore = threading.Semaphore(2)

        # TTL cache for metadata to avoid hammering Asset with duplicate requests
        self._metadata_cache: dict[str, tuple[float, str]] = {}
        self._metadata_cache_ttl = 5.0  # seconds
        self._metadata_cache_lock = threading.Lock()

        self._media_namespaces = {
            "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "dc": "http://purl.org/dc/elements/1.1/",
            "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
            "dlna": "urn:schemas-dlna-org:metadata-1-0/",
        }

    @property
    def name(self) -> str:
        return self._device.friendly_name

    @property
    def device(self):
        return self._device

    @property
    def device_state(self) -> MediaServerState:
        return MediaServerState(name=self._device.friendly_name)

    @property
    def device_udn(self) -> str:
        return self._device.udn.removeprefix("uuid:")

    def on_startup(self) -> None:
        pass

    def on_shutdown(self) -> None:
        pass

    # -------------------------------------------------------------------------
    # System

    def clear_caches(self):
        self._albums.cache_clear()
        self._new_albums.cache_clear()
        self._artists.cache_clear()
        self._tracks.cache_clear()

        with self._metadata_cache_lock:
            self._metadata_cache.clear()

    @property
    def url_prefix(self):
        media_location = self.device.location
        parsed_location = urlparse(media_location)

        return f"{parsed_location.scheme}://{parsed_location.netloc}"

    # -------------------------------------------------------------------------
    # Settings

    @property
    def all_albums_path(self) -> str | None:
        return self._all_albums_path

    @all_albums_path.setter
    def all_albums_path(self, path: str):
        self._all_albums_path = path

    @property
    def new_albums_path(self) -> str | None:
        return self._new_albums_path

    @new_albums_path.setter
    def new_albums_path(self, path: str):
        self._new_albums_path = path

    @property
    def all_artists_path(self) -> str | None:
        return self._all_artists_path

    @all_artists_path.setter
    def all_artists_path(self, path: str):
        self._all_artists_path = path

    # -------------------------------------------------------------------------
    # Media

    @lru_cache
    def _albums(self) -> list[Album]:
        if self.all_albums_path is None:
            return []

        all_albums = self.get_path_contents(Path(self.all_albums_path))
        self._albums_by_id = {album.id: album for album in all_albums}

        return all_albums

    @property
    def albums(self) -> list[Album]:
        return self._albums()

    @lru_cache
    def _new_albums(self) -> list[Album]:
        # NOTE: This could just return the results of:
        #
        #   self.get_path_contents(Path(self.new_albums_path))
        #
        # ... but "New Albums" returns albums with different Ids from the
        # "All Albums" path. So after getting the New Albums, we replace each
        # one with its equivalent from the "All Albums" list. This isn't ideal
        # as the comparison might produce a false positive (it's possible that
        # two distinct albums might share the same title/artist/etc).

        all_albums = self.albums
        new_albums = self.get_path_contents(Path(self.new_albums_path))

        def album_from_all(new_album):
            for album in all_albums:
                if (
                    album.title == new_album.title
                    and album.creator == new_album.creator
                    and album.date == new_album.date
                    and album.artist == new_album.artist
                    and album.genre == new_album.genre
                ):
                    return album

            return new_album

        return [album_from_all(new_album) for new_album in new_albums]

    @property
    def new_albums(self) -> list[Album]:
        return self._new_albums()

    def album_tracks(self, album_id) -> list[Track]:
        return sorted(
            [track for track in self.tracks if track.albumId == album_id],
            key=lambda track: track.original_track_number,
        )

    @lru_cache
    def _artists(self) -> list[Artist]:
        all_artists = self.get_path_contents(Path(self.all_artists_path))
        self._artists_by_id = {artist.id: artist for artist in all_artists}

        return all_artists

    @property
    def artists(self) -> list[Artist]:
        return self._artists()

    def artist(self, artist_id: str) -> Artist:
        try:
            return [artist for artist in self.artists if artist.id == artist_id][0]
        except IndexError:
            raise VibinNotFoundError(f"Could not find Artist with id '{artist_id}'")

    @lru_cache
    def _tracks(self) -> list[Track]:
        tracks: list[Track] = []

        # Retrieve all tracks by iterating over all albums. This ensures that
        # that each Track's albumId can be set properly.

        for album in self.albums:
            # Get XML descriptions of all tracks for this album.
            album_tracks_xml = self._get_children_xml(album.id)
            parsed_metadata = untangle.parse(album_tracks_xml)

            # Handle case where album has no tracks (item attribute won't exist)
            items = getattr(parsed_metadata.DIDL_Lite, "item", [])

            # Ensure items is iterable (single item vs list)
            if not isinstance(items, list):
                items = [items]

            album_tracks = [
                track
                for item in items
                if (track := self._track_from_item(item)) is not None
            ]

            for album_track in album_tracks:
                album_track.albumId = album.id

            tracks.extend(album_tracks)

        self._tracks_by_id = {track.id: track for track in tracks}

        return tracks

    @property
    def tracks(self) -> list[Track]:
        return self._tracks()

    def album(self, album_id: str) -> Album:
        try:
            return [album for album in self.albums if album.id == album_id][0]
        except IndexError:
            raise VibinNotFoundError(f"Could not find Album with id '{album_id}'")

    def track(self, track_id: str) -> Track:
        try:
            return [track for track in self.tracks if track.id == track_id][0]
        except IndexError:
            raise VibinNotFoundError(f"Could not find Track with id '{track_id}'")

    def ids_from_filename(
        self, filename: str, requested_ids: list[MediaType]
    ) -> dict[MediaType, MediaId]:
        stem = Path(filename).stem
        found_ids: dict[MediaType, MediaId] = {key: None for key in requested_ids}

        # Asset Ids seem to be of the form "d-123345...", and "co12A345...".
        # The first character is a letter, followed by an optional hyphen,
        # followed by one or more alphanumeric.
        potential_ids = re.findall(r"[a-z]-?[a-z0-9]+", stem, re.IGNORECASE)

        album_ids = self._albums_by_id.keys()
        artist_ids = self._artists_by_id.keys()
        track_ids = self._tracks_by_id.keys()

        for potential_id in potential_ids:
            if potential_id in album_ids:
                found_ids["album"] = potential_id
            elif potential_id in artist_ids:
                found_ids["artist"] = potential_id
            elif potential_id in track_ids:
                found_ids["track"] = potential_id

        if found_ids["album"] is None and found_ids["track"] is not None:
            # Attempt to find the album id from the track id.
            try:
                found_ids["album"] = self.track(found_ids["track"]).albumId
            except (VibinNotFoundError, AttributeError):
                pass

        return {key: value for key, value in found_ids.items() if key in requested_ids}

    # Browsing

    def get_path_contents(
        self, path
    ) -> list[MediaFolder | Artist | Album | Track] | Track | None:
        # TODO: This isn't really producing expected results. It attempts to
        #   convert (for example) a container.album into an Album, which isn't
        #   strictly accurate.
        parent_id = "0"

        for path_part in path.parts:
            parent_id, element_type = self._child_id_by_title(parent_id, path_part)

        leaf_id = parent_id

        if element_type == "container":
            children = untangle.parse(self._get_children_xml(leaf_id))

            contents = []

            if "container" in children.DIDL_Lite:
                for container in children.DIDL_Lite.container:
                    this_class = container.upnp_class.cdata

                    if this_class.startswith("object.container.album.musicAlbum"):
                        if album := self._album_from_container(container):
                            contents.append(album)
                    elif this_class.startswith("object.container.person.musicArtist"):
                        if artist := self._artist_from_container(container):
                            contents.append(artist)
                    elif this_class.startswith("object.container"):
                        if folder := self._folder_from_container(container):
                            contents.append(folder)
            elif "item" in children.DIDL_Lite:
                for item in children.DIDL_Lite.item:
                    this_class = item.upnp_class.cdata

                    if this_class == "object.item.audioItem.musicTrack":
                        if track := self._track_from_item(item):
                            contents.append(track)

            return contents
        elif element_type == "item":
            try:
                if track := self._track_from_metadata(self.get_metadata(leaf_id)):
                    return track

                return None
            except VibinNotFoundError as e:
                return None

        return None

    def children(self, parent_id: str = "0") -> MediaBrowseSingleLevel:
        return MediaBrowseSingleLevel(
            id=parent_id,
            children=self._children_xml_to_list(self._get_children_xml(parent_id)),
        )

    def get_metadata(self, id: str):
        now = time.time()

        # Check cache first
        with self._metadata_cache_lock:
            if id in self._metadata_cache:
                cached_time, cached_result = self._metadata_cache[id]

                if now - cached_time < self._metadata_cache_ttl:
                    return cached_result

        # Fetch from Asset
        try:
            result = self._sync_browse(id, "BrowseMetadata")
        except VibinSoapError as e:
            raise VibinNotFoundError(f"Could not find media id {id}") from e

        # Cache the result
        with self._metadata_cache_lock:
            self._metadata_cache[id] = (now, result)

            # Clean up old entries
            expired = [
                k
                for k, (t, _) in self._metadata_cache.items()
                if now - t >= self._metadata_cache_ttl
            ]

            for k in expired:
                del self._metadata_cache[k]

        return result

    def get_audio_file_url(self, track_id: MediaId) -> str | None:
        """Get the audio file URL for a track by MediaId."""
        try:
            metadata = self.get_metadata(track_id)
            track_info = xmltodict.parse(metadata)

            audio_files = [
                file
                for file in track_info["DIDL-Lite"]["item"]["res"]
                if file["#text"].endswith(".flac") or file["#text"].endswith(".wav")
            ]

            return audio_files[0]["#text"] if audio_files else None
        except (KeyError, IndexError, xml.parsers.expat.ExpatError, VibinNotFoundError):
            return None

    # -------------------------------------------------------------------------
    # UPnP

    def subscribe_to_upnp_events(self) -> None:
        pass

    @property
    def upnp_properties(self) -> UPnPProperties:
        return self._upnp_properties

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return {}

    def on_upnp_event(self, service_name: str, event: str):
        pass

    # -------------------------------------------------------------------------
    # Additional helpers (not part of MediaServer interface).
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Static

    @staticmethod
    def _folder_from_container(container) -> MediaFolder | None:
        """Convert a UPnP container to a MediaFolder."""
        try:
            return MediaFolder(
                creator=container.dc_creator.cdata,
                title=container.dc_title.cdata,
                album_art_uri=container.upnp_albumArtURI.cdata,
                artist=container.upnp_artist.cdata,
                class_field=container.upnp_class.cdata,
                genre=container.upnp_genre.cdata,
            )
        except AttributeError as e:
            logger.warning(
                f"Could not generate MediaFolder from XML container: {e} -> {container}"
            )

        return None

    @staticmethod
    def _album_from_container(container) -> Album | None:
        """Convert a UPnP container to an Album."""
        try:
            return Album(
                id=container["id"],
                title=container.dc_title.cdata,
                creator=container.dc_creator.cdata,
                date=container.dc_date.cdata,
                artist=container.upnp_artist.cdata,
                genre=container.upnp_genre.cdata,
                album_art_uri=container.upnp_albumArtURI.cdata,
            )
        except AttributeError as e:
            # Build a human-readable identifier from available attributes
            identifiers = []

            if hasattr(container, "dc_title"):
                identifiers.append(f"album={container.dc_title.cdata!r}")
            if hasattr(container, "upnp_artist"):
                identifiers.append(f"artist={container.upnp_artist.cdata!r}")
            elif hasattr(container, "dc_creator"):
                identifiers.append(f"creator={container.dc_creator.cdata!r}")

            identifiers.append(f"id={container['id']}")

            logger.warning(f"Could not generate Album ({', '.join(identifiers)}): {e}")

        return None

    @staticmethod
    def _artist_from_container(container) -> Artist | None:
        """Convert a UPnP container to an Artist."""
        try:
            return Artist(
                id=container["id"],
                title=container.dc_title.cdata,
                genre=container.upnp_genre.cdata,
                album_art_uri=container.upnp_albumArtURI.cdata,
            )
        except AttributeError as e:
            # Build a human-readable identifier from available attributes
            identifiers = []

            if hasattr(container, "dc_title"):
                identifiers.append(f"name={container.dc_title.cdata!r}")

            identifiers.append(f"id={container['id']}")

            logger.warning(f"Could not generate Artist ({', '.join(identifiers)}): {e}")

        return None

    @staticmethod
    def _playable_vibin_type(vibin_type: str) -> bool:
        """Determine whether the givin UPnP class is playable by Vibin."""
        # See ContentDirectory:4 spec, page 162, for all class names
        playable_upnp_classes = [
            "object.container.album.musicAlbum",
            "object.item.audioItem.musicTrack",
            "object.item.audioItem.audioBroadcast",
        ]

        return vibin_type in playable_upnp_classes

    @staticmethod
    def _track_from_item(item) -> Track | None:
        """Create a Track from a UPnP item's XML."""

        # Determine artist name. A single item can have multiple artists, each
        # with a different role ("AlbumArtist", "Composer", etc. The default
        # artist seems to have no role defined. The Track class currently only
        # supports a single artist, so attempt to pick one.
        #
        # Heuristic: Look for the artist with no role, otherwise pick the first
        #   artist. And if the artist info isn't an array then treat it as a
        #   normal field (and pull its cdata).

        artist = "<Unknown>"

        try:
            artist = next(
                (artist for artist in item.upnp_artist if artist["role"] is None),
                item.upnp_artist[0],
            ).cdata
        except IndexError:
            try:
                artist = item.upnp_artist.cdata
            except KeyError:
                pass

        # Asset Track Ids seem to be in "{trackId}-{parentId}" format. We strip
        # off the "-{parentId}" component, leaving just "{trackId}" (which is
        # still a valid unique Track Id). We then treat the parentId as the
        # Album Id for Vibin's purposes.

        try:
            return Track(
                id=item["id"].removesuffix(f"-{item['parentID']}"),
                albumId=item["parentID"],
                title=item.dc_title.cdata,
                creator=item.dc_creator.cdata,
                # date=item.dc_date.cdata if hasattr(item, "dc_date") else "(Unknown Date)",
                date=item.dc_date.cdata,
                artist=artist,
                album=item.upnp_album.cdata,
                duration=item.res[0]["duration"],
                genre=item.upnp_genre.cdata,
                album_art_uri=item.upnp_albumArtURI.cdata,
                # original_track_number=item.upnp_originalTrackNumber.cdata if hasattr(item, "upnp_originalTrackNumber") else 0,
                original_track_number=item.upnp_originalTrackNumber.cdata,
            )
        except AttributeError as e:
            # Build a human-readable identifier from available attributes
            identifiers = []

            if hasattr(item, "dc_title"):
                identifiers.append(f"title={item.dc_title.cdata!r}")
            if artist != "<Unknown>":
                identifiers.append(f"artist={artist!r}")
            if hasattr(item, "upnp_album"):
                identifiers.append(f"album={item.upnp_album.cdata!r}")

            identifiers.append(f"id={item['id']}")

            logger.warning(f"Could not generate Track ({', '.join(identifiers)}): {e}")

        return None

    # -------------------------------------------------------------------------

    def _album_from_metadata(self, metadata) -> Album | None:
        """Create an Album from the Media Server's item metadata."""
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.container.upnp_class.cdata
            != "object.container.album.musicAlbum"
        ):
            raise VibinNotFoundError(f"Could not find Album")

        return self._album_from_container(parsed_metadata.DIDL_Lite.container)

    def _artist_from_metadata(self, metadata) -> Artist | None:
        """Create an Artist from the Media Server's item metadata."""
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.container.upnp_class.cdata
            != "object.container.person.musicArtist"
        ):
            raise VibinNotFoundError(f"Could not find Artist")

        return self._artist_from_container(parsed_metadata.DIDL_Lite.container)

    def _track_from_metadata(self, metadata) -> Track | None:
        """Create a Track from the Media Server's item metadata."""
        parsed_metadata = untangle.parse(metadata)

        if (
            "item" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.item.upnp_class.cdata
            != "object.item.audioItem.musicTrack"
        ):
            raise VibinNotFoundError(f"Could not find Track")

        return self._track_from_item(parsed_metadata.DIDL_Lite.item)

    def _children_xml_to_list(self, xml: str) -> list[dict[str, Any]]:
        """Create a list of dicts, one per child, from the given xml."""
        elem_name_map = {
            "dc:title": "title",
            "dc:creator": "creator",
            "dc:date": "date",
            "upnp:artist": "artist",
            "upnp:album": "album",
            "upnp:genre": "genre",
            "upnp:albumArtURI": "album_art_uri",
            "upnp:originalTrackNumber": "original_track_number",
            "upnp:class": "vibin_type",
        }

        elems = ET.fromstring(xml)
        child_list = []

        for elem in elems:
            child_elem = {
                "id": elem.attrib["id"],
                "parent_id": elem.attrib["parentID"],
            }

            for xml_field, result_field in elem_name_map.items():
                value = self._xml_elem_field_value(elem, xml_field)

                if value is not None:
                    child_elem[result_field] = value

            try:
                child_elem["vibin_playable"] = self._playable_vibin_type(
                    child_elem["vibin_type"]
                )
            except KeyError:
                child_elem["vibin_playable"] = False

            child_elem["xml"] = ET.tostring(elem).decode("utf-8")

            child_list.append(child_elem)

        return child_list

    def _xml_elem_field_value(self, elem, field):
        """Extract a field's value from the given elem's XML."""
        find_result = elem.find(field, namespaces=self._media_namespaces)

        if not isinstance(find_result, ET.Element):
            return None

        value = find_result.text
        value = None if value == "" else value

        return value

    def _child_id_by_title(self, parent_id, title) -> (str, str):
        """Find a single child by title, under the given parent_id.

        Returns the child's id and type.
        """
        children_xml = self._get_children_xml(parent_id)
        root = ET.fromstring(children_xml)

        # Check for a container matching the given title
        found = root.find(
            f"didl:container/dc:title[.='{title}']..",
            namespaces=self._media_namespaces,
        )

        element_type = "container"

        # Check for an item (e.g. Track) matching the given title
        if not found:
            found = root.find(
                f"didl:item/dc:title[.='{title}']..",
                namespaces=self._media_namespaces,
            )

            element_type = "item"

        if not found:
            raise VibinNotFoundError(
                f"Could not find path '{title}' under container id {parent_id}"
            )

        return found.attrib["id"], element_type

    def _get_children_xml(self, id):
        """Get the children of the given id from the Media Server."""
        return self._sync_browse(id, "BrowseDirectChildren")

    def _sync_browse(self, object_id: str, browse_flag: str) -> str:
        """Perform a synchronous UPnP Browse action."""
        # Limit concurrent requests to prevent overwhelming Asset
        self._request_semaphore.acquire()

        # Track in-flight requests
        with self._inflight_lock:
            self._inflight_requests += 1
            inflight = self._inflight_requests

        start_time = time.time()
        flag_short = "Meta" if browse_flag == "BrowseMetadata" else "Children"
        logger.debug(
            f"Asset UPnP: {flag_short}({object_id}) started [inflight: {inflight}]"
        )

        async def _do_browse() -> str:
            async with aiohttp.ClientSession() as session:
                requester = AiohttpSessionRequester(session, with_sleep=True)
                factory = UpnpFactory(requester, non_strict=True)
                device = await factory.async_create_device(self._device.location)
                content_directory = device.service(
                    "urn:schemas-upnp-org:service:ContentDirectory:1"
                )
                result = await content_directory.async_call_action(
                    "Browse",
                    ObjectID=object_id,
                    BrowseFlag=browse_flag,
                    Filter="*",
                    StartingIndex=0,
                    RequestedCount=0,
                    SortCriteria="",
                )
                return result["Result"]

        try:
            result = run_coroutine_sync(_do_browse)()
            elapsed = time.time() - start_time
            logger.debug(
                f"Asset UPnP: {flag_short}({object_id}) completed in {elapsed:.2f}s"
            )

            return result
        except asyncio.CancelledError as e:
            elapsed = time.time() - start_time
            logger.warning(
                f"Asset UPnP: {flag_short}({object_id}) cancelled after {elapsed:.2f}s"
            )
            raise VibinMediaServerError("Media server request cancelled") from e
        except (UpnpConnectionError, UpnpConnectionTimeoutError) as e:
            elapsed = time.time() - start_time
            logger.warning(
                f"Asset UPnP: {flag_short}({object_id}) failed after {elapsed:.2f}s: {e}"
            )
            raise VibinMediaServerError(f"Media server connection failed: {e}") from e
        except (UpnpActionError, UpnpActionResponseError) as e:
            raise VibinSoapError(str(e), getattr(e, "error_code", None)) from e
        finally:
            with self._inflight_lock:
                self._inflight_requests -= 1

            self._request_semaphore.release()
