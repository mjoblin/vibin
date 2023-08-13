from functools import lru_cache
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import requests
import xml
import xmltodict

from vibin import VibinError, VibinMissingDependencyError
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.types import MediaId, WaveformFormat
from vibin.utils import requires_media_server


class WaveformManager:
    """Waveform manager.

    Generates track waveforms. Requires the audiowaveform binary to be
    installed and in the PATH.
    """

    def __init__(self, media_server: MediaServer):
        self._media_server = media_server

    # TODO: Investigate storing waveforms in a persistent cache/DB rather than
    #   relying on @lru_cache.

    @lru_cache
    @requires_media_server()
    def waveform_for_track(
        self,
        track_id: MediaId,
        data_format: WaveformFormat = "json",
        width: int = 800,
        height: int = 250,
    ) -> dict | str | bytes | None:
        """Generate the waveform for a track.

        The waveform can be an image (png) or raw text data (json or dat). The
        width and height parameters are used for png only.
        """
        try:
            track_info = xmltodict.parse(self._media_server.get_metadata(track_id))

            audio_files = [
                file
                for file in track_info["DIDL-Lite"]["item"]["res"]
                if file["#text"].endswith(".flac") or file["#text"].endswith(".wav")
            ]

            audio_file = audio_files[0]["#text"]

            # Retrieve the audio file and temporarily store it locally. Give the
            # audio file to the audiowaveform tool for processing.

            with tempfile.NamedTemporaryFile(prefix="vibin_", suffix=track_id) as flac_file:
                with requests.get(audio_file, stream=True) as response:
                    shutil.copyfileobj(response.raw, flac_file)

                # Explanation for 8-bit data (--bits 8):
                # https://github.com/bbc/peaks.js#pre-computed-waveform-data

                waveform_data = subprocess.run(
                    [
                        "audiowaveform",
                        "--bits",
                        "8",
                        "--input-filename",
                        str(Path(tempfile.gettempdir(), str(flac_file.name))),
                        "--input-format",
                        Path(audio_file).suffix[1:],
                        "--output-format",
                        data_format,
                    ]
                    + (
                        [
                            "--zoom",
                            "auto",
                            "--width",
                            str(width),
                            "--height",
                            str(height),
                            "--colors",
                            "audition",
                            "--split-channels",
                            "--no-axis-labels",
                        ]
                        if data_format == "png"
                        else []
                    ),
                    capture_output=True,
                )

                if data_format == "json":
                    return json.loads(waveform_data.stdout.decode("utf-8"))
                else:
                    return waveform_data.stdout
        except FileNotFoundError:
            raise VibinMissingDependencyError("audiowaveform")
        except KeyError as e:
            raise VibinError(f"Could not find any file information for track: {track_id}")
        except IndexError as e:
            raise VibinError(f"Could not find .flac or .wav file URL for track: {track_id}")
        except xml.parsers.expat.ExpatError as e:
            logger.error(f"Could not convert XML to JSON for track: {track_id}: {e}")
        except VibinError as e:
            logger.error(e)

        return None
