import logging


# Configure logging
logging.Formatter.default_time_format = '%Y-%m-%dT%H:%M:%S'
logging.Formatter.default_msec_format = '%s.%03d'

log_formatter = logging.Formatter(
    "%(asctime)s %(name)s [%(levelname)s] %(message)s"
)
log_handler = logging.StreamHandler()
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("vibin")
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)

# Have the upnpclient loggers adhere to the vibin log format.
ssdp_logger = logging.getLogger("ssdp")
soap_logger = logging.getLogger("Soap")
device_logger = logging.getLogger("Device")
ssdp_logger.addHandler(log_handler)
soap_logger.addHandler(log_handler)
device_logger.addHandler(log_handler)
