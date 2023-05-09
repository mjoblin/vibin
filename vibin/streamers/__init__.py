from .streamer import Streamer

# Each Streamer implementation should be imported here.
from .streammagic import StreamMagic

# Map UPnP device models to Streamer implementations. This is not required if
# the UPnP model name is the same as the implementation name.
model_to_streamer = {
    "CXNv2": StreamMagic,
}
