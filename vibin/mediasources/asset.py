import typing
import xml.etree.ElementTree as ET

from vibin import VibinError
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

    @property
    def albums(self) -> typing.List[Album]:
        if self._albums is not None:
            return list(self._albums.values())

        # Retrieve full album list from media source.
        # TODO: This assumes "Album / [All Albums]"; consider adding a way to
        #   override this path.
        self._albums = {}

        by_album_id = self._child_id_by_title("0", "Album")
        all_albums_id = self._child_id_by_title(by_album_id, "[All Albums]")

        all_albums_xml = self._get_children_xml(all_albums_id)
        all_albums_elems = ET.fromstring(all_albums_xml)

        for album_elem in all_albums_elems:
            album_id = album_elem.attrib["id"]

            album = Album(
                album_id,
                album_elem.find("dc:title", namespaces=self._media_namespaces ).text,
                album_elem.find("dc:creator", namespaces=self._media_namespaces ).text,
                album_elem.find("dc:date", namespaces=self._media_namespaces ).text,
                album_elem.find("upnp:artist", namespaces=self._media_namespaces ).text,
                album_elem.find("upnp:genre", namespaces=self._media_namespaces ).text,
                album_elem.find("upnp:albumArtURI", namespaces=self._media_namespaces ).text,
            )

            self._albums[album_id] = album

        return list(self._albums.values())

    def tracks(self, album) -> typing.List[Track]:
        album_tracks_xml = self._get_children_xml(album.id)
        album_tracks_elems = ET.fromstring(album_tracks_xml)
        all_tracks = []

        for track_elem in album_tracks_elems:
            track = Track(
                track_elem.attrib["id"],
                track_elem.find("dc:title", namespaces=self._media_namespaces).text,
                track_elem.find("dc:creator", namespaces=self._media_namespaces).text,
                track_elem.find("dc:date", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:artist", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:album", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:genre", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:albumArtURI", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:originalTrackNumber", namespaces=self._media_namespaces).text,
            )

            all_tracks.append(track)

        return all_tracks

    def children(self, parent_id: str = "0"):
        # TODO: Should this return Container, Album, and Track types?
        return {
            "id": parent_id,
            "children": self._child_xml_to_list(self._get_children_xml(parent_id)),
        }

    def get_metadata(self, id: str):
        browse_result = self._device.ContentDirectory.Browse(
            ObjectID=id,
            BrowseFlag="BrowseMetadata",
            Filter="*",
            StartingIndex=0,
            RequestedCount=0,
            SortCriteria="",
        )

        return browse_result["Result"]

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
            raise VibinError(
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
