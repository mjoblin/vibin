from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import upnpclient
import untangle

from vibin import VibinNotFoundError
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
from vibin.types import UpdateMessageHandler, UPnPProperties


# https://dbpoweramp.com/asset-upnp-dlna.htm


class Asset(MediaServer):
    model_name = "Asset UPnP Server"

    def __init__(
        self,
        device: upnpclient.Device,
        subscribe_callback_base: str | None = None,
        on_update: UpdateMessageHandler | None = None,
    ):
        self._device: upnpclient.Device = device

        self._all_albums_path: str | None = None
        self._new_albums_path: str | None = None
        self._all_artists_path: str | None = None

        self._upnp_properties: UPnPProperties = {}

        self._media_namespaces = {
            "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "dc": "http://purl.org/dc/elements/1.1/",
            "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
            "dlna": "urn:schemas-dlna-org:metadata-1-0/",
        }

    @property
    def upnp_properties(self) -> UPnPProperties:
        return self._upnp_properties

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

    @property
    def name(self) -> str:
        return self._device.friendly_name

    @property
    def device(self):
        return self._device

    @property
    def url_prefix(self):
        media_location = self.device.location
        parsed_location = urlparse(media_location)

        return f"{parsed_location.scheme}://{parsed_location.netloc}"

    @property
    def device_state(self) -> MediaServerState:
        return MediaServerState(name=self._device.friendly_name)

    @property
    def upnp_subscriptions(self) -> UPnPServiceSubscriptions:
        return {}

    @property
    def device_udn(self) -> str:
        return self._device.udn.removeprefix("uuid:")

    def clear_caches(self):
        self._albums.cache_clear()
        self._new_albums.cache_clear()
        self._artists.cache_clear()
        self._tracks.cache_clear()

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
                        contents.append(self._album_from_container(container))
                    elif this_class.startswith("object.container.person.musicArtist"):
                        contents.append(self._artist_from_container(container))
                    elif this_class.startswith("object.container"):
                        contents.append(self._folder_from_container(container))
            elif "item" in children.DIDL_Lite:
                for item in children.DIDL_Lite.item:
                    this_class = item.upnp_class.cdata

                    if this_class == "object.item.audioItem.musicTrack":
                        contents.append(self._track_from_item(item))

            return contents
        elif element_type == "item":
            try:
                return self._track_from_metadata(self.get_metadata(leaf_id))
            except VibinNotFoundError as e:
                return None

        return None

    @lru_cache
    def _albums(self) -> list[Album]:
        if self.all_albums_path is None:
            return []

        return self.get_path_contents(Path(self.all_albums_path))

    @property
    def albums(self) -> list[Album]:
        return self._albums()

    @lru_cache
    def _new_albums(self) -> list[Album]:
        # NOTE: This could just:
        #
        #   return self.get_path_contents(Path(self.new_albums_path))
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
        album_tracks_xml = self._get_children_xml(album_id)
        parsed_metadata = untangle.parse(album_tracks_xml)

        return [self._track_from_item(item) for item in parsed_metadata.DIDL_Lite.item]

    @lru_cache
    def _artists(self) -> list[Artist]:
        return self.get_path_contents(Path(self.all_artists_path))

    @property
    def artists(self) -> list[Artist]:
        return self._artists()

    def artist(self, artist_id: str) -> Artist:
        try:
            return self._artist_from_metadata(self.get_metadata(artist_id))
        except VibinNotFoundError as e:
            raise VibinNotFoundError(f"Could not find Artist with id '{artist_id}'")

    @lru_cache
    def _tracks(self) -> list[Track]:
        tracks: list[Track] = []

        for album in self.albums:
            tracks.extend(self.album_tracks(album.id))

        return tracks

    @property
    def tracks(self) -> list[Track]:
        return self._tracks()

    @staticmethod
    def _folder_from_container(container) -> MediaFolder:
        return MediaFolder(
            creator=container.dc_creator.cdata,
            title=container.dc_title.cdata,
            album_art_uri=container.upnp_albumArtURI.cdata,
            artist=container.upnp_artist.cdata,
            class_field=container.upnp_class.cdata,
            genre=container.upnp_genre.cdata,
        )

    @staticmethod
    def _album_from_container(container) -> Album:
        return Album(
            id=container["id"],
            parentId=container["parentID"],
            title=container.dc_title.cdata,
            creator=container.dc_creator.cdata,
            date=container.dc_date.cdata,
            artist=container.upnp_artist.cdata,
            genre=container.upnp_genre.cdata,
            album_art_uri=container.upnp_albumArtURI.cdata,
        )

    @staticmethod
    def _artist_from_container(container) -> Artist:
        return Artist(
            id=container["id"],
            parentId=container["parentID"],
            title=container.dc_title.cdata,
            genre=container.upnp_genre.cdata,
            album_art_uri=container.upnp_albumArtURI.cdata,
        )

    def _album_from_metadata(self, metadata) -> Album:
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.container.upnp_class.cdata
            != "object.container.album.musicAlbum"
        ):
            raise VibinNotFoundError(f"Could not find Album")

        return self._album_from_container(parsed_metadata.DIDL_Lite.container)

    def _artist_from_metadata(self, metadata) -> Artist:
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.container.upnp_class.cdata
            != "object.container.person.musicArtist"
        ):
            raise VibinNotFoundError(f"Could not find Artist")

        return self._artist_from_container(parsed_metadata.DIDL_Lite.container)

    @staticmethod
    def _track_from_item(item) -> Track:
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

        return Track(
            id=item["id"],
            parentId=item["parentID"],
            title=item.dc_title.cdata,
            creator=item.dc_creator.cdata,
            date=item.dc_date.cdata,
            artist=artist,
            album=item.upnp_album.cdata,
            duration=item.res[0]["duration"],
            genre=item.upnp_genre.cdata,
            album_art_uri=item.upnp_albumArtURI.cdata,
            original_track_number=item.upnp_originalTrackNumber.cdata,
        )

    def _track_from_metadata(self, metadata) -> Track:
        parsed_metadata = untangle.parse(metadata)

        if (
            "item" not in parsed_metadata.DIDL_Lite
            or parsed_metadata.DIDL_Lite.item.upnp_class.cdata
            != "object.item.audioItem.musicTrack"
        ):
            raise VibinNotFoundError(f"Could not find Track")

        return self._track_from_item(parsed_metadata.DIDL_Lite.item)

    def album(self, album_id: str) -> Album:
        try:
            return self._album_from_metadata(self.get_metadata(album_id))
        except VibinNotFoundError as e:
            raise VibinNotFoundError(f"Could not find Album with id '{album_id}'")

    def track(self, track_id: str) -> Track:
        try:
            return self._track_from_metadata(self.get_metadata(track_id))
        except VibinNotFoundError as e:
            raise VibinNotFoundError(f"Could not find Track with id '{track_id}'")

    def children(self, parent_id: str = "0") -> MediaBrowseSingleLevel:
        return MediaBrowseSingleLevel(
            id=parent_id,
            children=self._child_xml_to_list(self._get_children_xml(parent_id)),
        )

    def get_metadata(self, id: str):
        try:
            browse_result = self._device.ContentDirectory.Browse(
                ObjectID=id,
                BrowseFlag="BrowseMetadata",
                Filter="*",
                StartingIndex=0,
                RequestedCount=0,
                SortCriteria="",
            )

            return browse_result["Result"]
        except upnpclient.soap.SOAPProtocolError as e:
            raise VibinNotFoundError(f"Could not find media id {id}")

    @staticmethod
    def _playable_vibin_type(vibin_type: str):
        # See ContentDirectory:4 spec, page 162, for all class names
        playable_upnp_classes = [
            "object.container.album.musicAlbum",
            "object.item.audioItem.musicTrack",
            "object.item.audioItem.audioBroadcast",
        ]

        return vibin_type in playable_upnp_classes

    def _child_xml_to_list(self, xml: str):
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
        find_result = elem.find(field, namespaces=self._media_namespaces)

        if not isinstance(find_result, ET.Element):
            return None

        value = find_result.text
        value = None if value == "" else value

        return value

    def _child_id_by_title(self, parent_id, title):
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
        browse_result = self._device.ContentDirectory.Browse(
            ObjectID=id,
            BrowseFlag="BrowseDirectChildren",
            Filter="*",
            StartingIndex=0,
            RequestedCount=0,
            SortCriteria="",
        )

        return browse_result["Result"]
