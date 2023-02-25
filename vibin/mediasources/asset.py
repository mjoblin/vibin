from pathlib import Path
import typing
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
            for item in children.DIDL_Lite.container:
                item_class = item.upnp_class.cdata

                if item_class == "object.container.album.musicAlbum":
                    contents.append(
                        Album(
                            item["id"],
                            item.dc_title.cdata,
                            item.dc_creator.cdata,
                            item.dc_date.cdata,
                            item.upnp_artist.cdata,
                            item.upnp_genre.cdata,
                            item.upnp_albumArtURI.cdata,
                        )
                    )
        elif "item" in children.DIDL_Lite:
            for item in children.DIDL_Lite.item:
                item_class = item.upnp_class.cdata

                # Determine artist name. A single item can have multiple
                # artists, each with a different role ("AlbumArtist",
                # "Composer", etc. The default artist seems to have no role
                # defined. The Track class currently only supports a single
                # artist, so attempt to pick one.
                #
                # Heuristic: Look for the artist with no role, otherwise pick
                #   the first artist. And if the artist info isn't an array
                #   then treat it as a normal field (and pull its cdata).

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

                if item_class == "object.item.audioItem.musicTrack":
                    contents.append(
                        Track(
                            item["id"],
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
                    )

        return contents

    @property
    def albums(self) -> typing.List[Album]:
        return self.get_path_contents(Path("Album", "[All Albums]"))

    @property
    def new_albums(self) -> typing.List[Album]:
        return self.get_path_contents(Path("New Albums"))

    def tracks(self, album_id) -> typing.List[Track]:
        album_tracks_xml = self._get_children_xml(album_id)
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
                track_elem.find("didl:res", namespaces=self._media_namespaces).attrib["duration"],
                track_elem.find("upnp:genre", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:albumArtURI", namespaces=self._media_namespaces).text,
                track_elem.find("upnp:originalTrackNumber", namespaces=self._media_namespaces).text,
            )

            # track_elem.find("didl:res", namespaces=self._media_namespaces).attrib["duration"]
            # available: size, bitrate, bitsPerSample, sampleFrequency, nrAudioChannels, protocolInfo
            #
            # {
            #   'duration': '0:03:36.000',
            #   'size': '15892752',
            #   'bitrate': '176400',
            #   'bitsPerSample': '16',
            #   'sampleFrequency': '44100',
            #   'nrAudioChannels': '2',
            #   'protocolInfo': 'http-get:*:audio/x-flac:DLNA.ORG_PN=FLAC;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=03700000000000000000000000000000'
            # }

            all_tracks.append(track)

        return all_tracks

    def _album_from_metadata(self, metadata) -> Album:
        parsed_metadata = untangle.parse(metadata)

        if (
            "container" not in parsed_metadata.DIDL_Lite or
            parsed_metadata.DIDL_Lite.container.upnp_class.cdata != "object.container.album.musicAlbum"
        ):
            raise VibinNotFoundError(f"Could not find Album")

        container = parsed_metadata.DIDL_Lite.container

        return Album(
            container["id"],
            container.dc_title.cdata,
            container.dc_creator.cdata,
            container.dc_date.cdata,
            container.upnp_artist.cdata,
            container.upnp_genre.cdata,
            container.upnp_albumArtURI.cdata,
        )

    def _track_from_metadata(self, metadata) -> Track:
        parsed_metadata = untangle.parse(metadata)

        if (
            "item" not in parsed_metadata.DIDL_Lite or
            parsed_metadata.DIDL_Lite.item.upnp_class.cdata != "object.item.audioItem.musicTrack"
        ):
            raise VibinNotFoundError(f"Could not find Track")

        item = parsed_metadata.DIDL_Lite.item

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

    def album(self, album_id: str) -> Album:
        return self._album_from_metadata(self.get_metadata(album_id))

    def track(self, track_id: str) -> Track:
        return self._track_from_metadata(self.get_metadata(track_id))

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
