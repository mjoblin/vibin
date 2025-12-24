from functools import lru_cache
import json
import pathlib
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import requests

from vibin import VibinError, VibinMissingDependencyError
from vibin.logger import logger
from vibin.mediaservers import MediaServer
from vibin.types import MediaId, WaveformFormat
from vibin.utils import requires_media_server


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https")


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
        track_id_or_url: MediaId | str,
        data_format: WaveformFormat = "json",
        width: int = 800,
        height: int = 250,
    ) -> dict | str | bytes | None:
        """Generate the waveform for a track.

        Args:
            track_id_or_url: Either a MediaId (track ID) or a direct URL to an
                audio file. URLs are detected via urlparse (must have http/https
                scheme). If a MediaId is provided, the audio URL is looked up
                from the media server.
            data_format: Output format - "json", "dat", or "png".
            width: Width for png output.
            height: Height for png output.

        Returns:
            Waveform data as dict (json), str (dat), or bytes (png), or None on error.
        """
        try:
            if _is_url(track_id_or_url):
                audio_file = track_id_or_url
            else:
                audio_file = self._media_server.get_audio_file_url(track_id_or_url)
                if not audio_file:
                    raise VibinError(
                        f"Could not find audio file URL for track: {track_id_or_url}"
                    )

            # Retrieve the audio file and temporarily store it locally. Give the
            # audio file to the audiowaveform tool for processing.

            with tempfile.NamedTemporaryFile(
                prefix="vibin_", suffix=pathlib.Path(audio_file).suffix
            ) as audio_temp_file:
                with requests.get(audio_file, stream=True) as response:
                    shutil.copyfileobj(response.raw, audio_temp_file)

                # Explanation for 8-bit data (--bits 8):
                # https://github.com/bbc/peaks.js#pre-computed-waveform-data

                waveform_data = subprocess.run(
                    [
                        "audiowaveform",
                        "--bits",
                        "8",
                        "--input-filename",
                        audio_temp_file.name,
                        "--input-format",
                        pathlib.Path(audio_file).suffix[1:],
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

                if waveform_data.returncode != 0:
                    error_msg = f"[code: {waveform_data.returncode}]"

                    if waveform_data.stderr:
                        error_msg += f" {waveform_data.stderr.decode('utf-8')}"

                    raise VibinError(f"Error running audiowaveform tool: {error_msg}")

                if data_format == "json":
                    try:
                        return json.loads(waveform_data.stdout.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        raise VibinError(
                            f"Got invalid JSON from audiowaveform tool: {e}"
                        )
                else:
                    return waveform_data.stdout
        except FileNotFoundError:
            raise VibinMissingDependencyError("audiowaveform")
        except VibinError as e:
            logger.error(e)
            raise

        return None
