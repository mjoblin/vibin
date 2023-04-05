from collections.abc import Iterable
import dataclasses
import math
import re

from pydantic import BaseModel

ONE_HOUR_IN_SECS = 60 * 60
ONE_MIN_IN_SECS = 60
HMMSS_MATCH = re.compile("^\d+:\d{2}:\d{2}(\.\d+)?$")


def is_hmmss(input: str) -> bool:
    return bool(HMMSS_MATCH.match(input))


def secs_to_hmmss(input_secs: int) -> str:
    hours = math.floor(input_secs / ONE_HOUR_IN_SECS)
    mins = math.floor(
        (input_secs - hours * ONE_HOUR_IN_SECS) / ONE_MIN_IN_SECS
    )
    secs = input_secs - (hours * ONE_HOUR_IN_SECS) - (mins * ONE_MIN_IN_SECS)

    return f"{hours}:{mins:02}:{secs:02}"


def hmmss_to_secs(input_hmmss: str) -> int:
    if not is_hmmss(input_hmmss):
        raise TypeError("Time must be in h:mm:ss format")

    [h, mm, ss] = [float(component) for component in input_hmmss.split(":")]

    return round(h * ONE_HOUR_IN_SECS + mm * ONE_MIN_IN_SECS + ss)


def replace_media_server_urls_with_proxy(payload, media_server_url_prefix):
    def transform(item):
        item_is_iterable = isinstance(item, Iterable)

        if isinstance(item, BaseModel) or dataclasses.is_dataclass(item):
            # The item is a data class or a pydantic model

            uri_attrs = ["album_art_uri", "albumArtURI", "uri", "art_url"]

            # TODO: Extend this case to work like iterables, which support
            #   transforming nested fields as well as transforming any
            #   string value which starts with the proxy target (rather
            #   than just a hardcoded list of attr names like is done here)

            for uri_attr in uri_attrs:
                if hasattr(item, uri_attr):
                    setattr(
                        item,
                        uri_attr,
                        getattr(item, uri_attr).replace(media_server_url_prefix, "/proxy")
                    )
        elif item_is_iterable:
            # The item is a dict, list, or string.
            if isinstance(item, dict):
                # If the item is a dict
                for key, value in item.items():
                    if isinstance(value, str) and value.startswith(media_server_url_prefix):
                        item[key] = value.replace(media_server_url_prefix, "/proxy")
                    elif isinstance(value, Iterable):
                        item[key] = transform(value)
            elif isinstance(item, list):
                # If the item is a list
                return [transform(child) for child in item]
            else:
                # Probably a string
                return item

        return item

    return transform(payload)
