from copy import copy
import logging
import sys
from typing import Literal, Optional

import click


# -----------------------------------------------------------------------------
# COPIED FROM uvicorn.logging
#
# Goal: To support log line colors, but for levelname as well and not just
#   levelprefix (levelprefix adds extra whitespace and a colon which isn't
#   desirable here).

TRACE_LOG_LEVEL = 5


class ColourizedFormatter(logging.Formatter):
    """
    A custom log formatter class that:

    * Outputs the LOG_LEVEL with an appropriate color.
    * If a log call includes an `extras={"color_message": ...}` it will be used
      for formatting the output, instead of the plain text message.
    """

    level_name_colors = {
        TRACE_LOG_LEVEL: lambda level_name: click.style(str(level_name), fg="blue"),
        logging.DEBUG: lambda level_name: click.style(str(level_name), fg="cyan"),
        # logging.INFO: lambda level_name: click.style(str(level_name), fg="green"),
        logging.INFO: lambda level_name: level_name,
        # logging.WARNING: lambda level_name: click.style(str(level_name), fg="yellow"),
        logging.WARNING: lambda level_name: click.style(str(level_name), fg="bright_yellow"),
        logging.ERROR: lambda level_name: click.style(str(level_name), fg="red"),
        logging.CRITICAL: lambda level_name: click.style(
            str(level_name), fg="bright_red"
        ),
    }

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: Literal["%", "{", "$"] = "%",
        use_colors: Optional[bool] = None,
    ):
        if use_colors in (True, False):
            self.use_colors = use_colors
        else:
            self.use_colors = sys.stdout.isatty()
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def color_level_name(self, level_name: str, level_no: int) -> str:
        def default(level_name: str) -> str:
            return str(level_name)  # pragma: no cover

        func = self.level_name_colors.get(level_no, default)
        return func(level_name)

    def should_use_colors(self) -> bool:
        return True  # pragma: no cover

    def formatMessage(self, record: logging.LogRecord) -> str:
        recordcopy = copy(record)
        levelname = recordcopy.levelname
        seperator = " " * (8 - len(recordcopy.levelname))
        if self.use_colors:
            levelname = self.color_level_name(levelname, recordcopy.levelno)
            if "color_message" in recordcopy.__dict__:
                recordcopy.msg = recordcopy.__dict__["color_message"]
                recordcopy.__dict__["message"] = recordcopy.getMessage()
        recordcopy.__dict__["levelprefix"] = levelname + ":" + seperator
        recordcopy.__dict__["levelname"] = levelname
        return super().formatMessage(recordcopy)

# END OF COPY
# -----------------------------------------------------------------------------


# Configure logging
logging.Formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
logging.Formatter.default_msec_format = "%s.%03d"

log_formatter = ColourizedFormatter(
    "%(asctime)s %(name)s [%(levelname)s] %(message)s"
)
log_handler = logging.StreamHandler()
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("vibin")
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)

# Have the uvicorn logger adhere to the vibin log format.
uvicorn_logger = logging.getLogger("uvicorn")

# Have the upnpclient loggers adhere to the vibin log format.
ssdp_logger = logging.getLogger("ssdp")
soap_logger = logging.getLogger("Soap")
device_logger = logging.getLogger("Device")
ssdp_logger.addHandler(log_handler)
soap_logger.addHandler(log_handler)
device_logger.addHandler(log_handler)
uvicorn_logger.addHandler(log_handler)
