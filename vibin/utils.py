from collections.abc import Iterable
import dataclasses
from distutils.version import StrictVersion
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import threading
import zipfile

from pydantic import BaseModel
import requests

from vibin import VibinError
from vibin.constants import UI_APPNAME, UI_BUILD_DIR, UI_REPOSITORY, UI_ROOT
from .logger import logger

ONE_HOUR_IN_SECS = 60 * 60
ONE_MIN_IN_SECS = 60
HMMSS_MATCH = re.compile("^\d+:\d{2}:\d{2}(\.\d+)?$")


class StoppableThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def stopped(self):
        return self.stop_event.is_set()


def get_local_ip():
    # https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()

    return ip


def is_hmmss(input: str) -> bool:
    return bool(HMMSS_MATCH.match(input))


def secs_to_hmmss(input_secs: int) -> str:
    hours = math.floor(input_secs / ONE_HOUR_IN_SECS)
    mins = math.floor((input_secs - hours * ONE_HOUR_IN_SECS) / ONE_MIN_IN_SECS)
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
                        getattr(item, uri_attr).replace(
                            media_server_url_prefix, "/proxy"
                        ),
                    )
        elif item_is_iterable:
            # The item is a dict, list, or string.
            if isinstance(item, dict):
                # If the item is a dict
                for key, value in item.items():
                    if isinstance(value, str) and value.startswith(
                        media_server_url_prefix
                    ):
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


def install_vibinui():
    logger.info(f"Installing the Web UI into: {UI_ROOT}")

    # Create the UI root directory if it doesn't already exist
    os.makedirs(UI_ROOT, exist_ok=True)

    # Call the GitHub API to get the tag name for "latest"
    try:
        logger.info(
            f"Retrieving latest version tag from GitHub repository ({UI_REPOSITORY})..."
        )
        response = requests.get(
            f"https://api.github.com/repos/{UI_REPOSITORY}/releases/latest"
        )
        api_response = response.json()

        latest_tag = api_response["tag_name"]
        logger.info(f"Installing version {latest_tag}")
    except (requests.RequestException, json.JSONDecodeError, KeyError):
        raise VibinError("Could not determine latest UI release tag from GitHub")

    # Download and extract the files.
    try:
        # Build the path to the latest release archive zipfile
        latest_zip = f"https://github.com/{UI_REPOSITORY}/archive/{latest_tag}.zip"
        logger.info(f"Downloading {latest_tag} archive from GitHub...")

        with tempfile.TemporaryFile() as local_ui_zipfile:
            # Download the latest release archive zipfile
            with requests.get(latest_zip, stream=True) as response:
                shutil.copyfileobj(response.raw, local_ui_zipfile)

            # Extract the build directory from the zipfile to the requested location
            with zipfile.ZipFile(local_ui_zipfile, "r") as zip_data:
                logger.info(f"Unpacking files...")

                top_level_zip_dir = zip_data.filelist[0].filename
                ui_install_dir = Path(UI_ROOT, top_level_zip_dir)

                if ui_install_dir.is_dir():
                    raise VibinError(
                        f"Install directory already exists: {ui_install_dir}"
                    )

                ui_build_files = [
                    file for file in zip_data.namelist() if UI_BUILD_DIR in file
                ]

                if len(ui_build_files) <= 0:
                    raise VibinError(
                        f"Web UI archive does not contain any '{UI_BUILD_DIR}' files"
                    )

                for ui_build_file in ui_build_files:
                    zip_data.extract(ui_build_file, path=UI_ROOT)

            logger.info(f"Web UI {latest_tag} installed into: {ui_install_dir}")
            logger.info(
                f"Specify '--vibinui auto' when running 'vibin serve' to serve this UI instance"
            )
    except requests.RequestException as e:
        raise VibinError(
            f"Could not download the {latest_tag} release from GitHub: {e}"
        )
    except zipfile.BadZipFile:
        raise VibinError(
            f"The downloaded UI archive does not appear to be a valid zipfile"
        )


def get_ui_install_dir() -> Path | None:
    try:
        candidates = [
            uidir
            for uidir in os.listdir(UI_ROOT)
            if uidir.startswith(UI_APPNAME) and os.path.isdir(Path(UI_ROOT, uidir))
        ]
    except FileNotFoundError:
        return None

    candidate_versions = [
        candidate.replace(f"{UI_APPNAME}-", "") for candidate in candidates
    ]

    candidate_versions.sort(key=StrictVersion, reverse=True)

    try:
        return Path(UI_ROOT, f"{UI_APPNAME}-{candidate_versions[0]}")
    except IndexError:
        return None
