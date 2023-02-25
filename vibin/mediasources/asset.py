from pathlib import Path
import typing
import upnpclient
import xml.etree.ElementTree as ET

import untangle

from vibin import VibinNotFoundError
from vibin.mediasources import MediaSource
from vibin.models import Album, Track


class Asset(MediaSource):
    model_name = "Asset UPnP Server"

    def __init__(self, device):
        self._device = device

        self._albums: typing.Optional[dict[str, Album]] = None

        self._media_namespaces = {
            "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "dc": "http://purl.org/dc/elements/1.1/",
            "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
            "dlna": "urn:schemas-dlna-org:metadata-1-0/",
        }

    @property
    def name(self):
        return self._device.friendly_name

    @property
    def udn(self):
        return self._device.udn.removeprefix("uuid:")

    def get_path_contents(self, path):
        parent_id = "0"

        for path_part in path.parts:
            parent_id = self._child_id_by_title(parent_id, path_part)

        leaf_id = parent_id
        children = untangle.parse(self._get_children_xml(leaf_id))

        contents = []

        if "container" in children.DIDL_Lite:
            for container in children.DIDL_Lite.container:
                this_class = container.upnp_class.cdata

                if this_class == "object.container.album.musicAlbum":
                    contents.append(self._album_from_container(container))
                elif this_class == "object.container":
                    contents.append(self._folder_from_container(container))
        elif "item" in children.DIDL_Lite:
            for item in children.DIDL_Lite.item:
                this_class = item.upnp_class.cdata

                if this_class == "object.item.audioItem.musicTrack":
                    contents.append(self._track_from_item(item))

        return contents

    @property
    def albums(self) -> typing.List[Album]:
        return self.get_path_contents(Path("Album", "[All Albums]"))

    @property
    def new_albums(self) -> typing.List[Album]:
        return self.get_path_contents(Path("New Albums"))

    def tracks(self, album_id) -> typing.List[Track]:
        album_tracks_xml = self._get_children_xml(album_id)
        parsed_metadata = untangle.parse(album_tracks_xml)

        return [
            self._track_from_item(item)
            for item in parsed_metadata.DIDL_Lite.item
        ]

    def _folder_from_container(self, container):
        return {
            "creator": container.dc_creator.cdata,
            "title": container.dc_title.cdata,
            "album_art_uri": container.upnp_albumArtURI.cdata,
            "artist": container.upnp_artist.cdata,
            "class": container.upnp_class.cdata,
            "genre": container.upnp_genre.cdata,
        }

    def _album_from_container(self, container) -> Album:
        return Album(
            container["id"],
            container["parentID"],
            container.dc_title.cdata,
            container.dc_creator.cdata,
            container.dc_date.cdata,
            container.upnp_artist.cdata,
            container.upnp_genre.cdata,
            container.upnp_albumArtURI.cdata,
        )

    def _album_from_metadata(self, metadata) -> Album:
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite or
            parsed_metadata.DIDL_Lite.container.upnp_class.cdata != "object.container.album.musicAlbum"
        ):
            raise VibinNotFoundError(f"Could not find Album")

        return self._album_from_container(parsed_metadata.DIDL_Lite.container)

    def _track_from_item(self, item) -> Track:
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
                item.upnp_artist[0]
            ).cdata
        except IndexError:
            try:
                artist = item.upnp_artist.cdata
            except KeyError:
                pass

        return Track(
            item["id"],
            item["parentID"],
            item.dc_title.cdata,
            item.dc_creator.cdata,
            item.dc_date.cdata,
            artist,
            item.upnp_album.cdata,
            item.res[0]["duration"],
            item.upnp_genre.cdata,
            item.upnp_albumArtURI.cdata,
            item.upnp_originalTrackNumber.cdata,
        )

    def _track_from_metadata(self, metadata) -> Track:
        parsed_metadata = untangle.parse(metadata)

        if (
            "item" not in parsed_metadata.DIDL_Lite or
            parsed_metadata.DIDL_Lite.item.upnp_class.cdata != "object.item.audioItem.musicTrack"
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

    def children(self, parent_id: str = "0"):
        # TODO: Should this return Container, Album, and Track types?
        return {
            "id": parent_id,
            "children": self._child_xml_to_list(self._get_children_xml(parent_id)),
        }

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
                value = self._xml_elem_field_value(elem, xml_field )

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

        found = root.find(
            f"didl:container/dc:title[.='{title}']..",
            namespaces=self._media_namespaces,
        )

        if not found:
            raise VibinNotFoundError(
                f"Could not find path '{title}' under container id {parent_id}"
            )

        return found.attrib["id"]

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
