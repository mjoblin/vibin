import math
import re

ONE_HOUR_IN_SECS = 60 * 60
ONE_MIN_IN_SECS = 60
HMMSS_MATCH = re.compile("^\d+:\d{2}:\d{2}$")


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

    [h, mm, ss] = [int(component) for component in input_hmmss.split(":")]

    return h * ONE_HOUR_IN_SECS + mm * ONE_MIN_IN_SECS + ss
