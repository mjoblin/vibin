from .mediaserver import MediaServer

# Each MediaServer implementation should be imported here.
from .asset import Asset
from .cxnv2usb import CXNv2USB

# Map UPnP device models to Media Server implementations. This is not required
# if the UPnP model name is the same as the implementation name.
model_to_media_server = {"CXNv2": CXNv2USB}
