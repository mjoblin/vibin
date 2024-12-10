from .streamer import Streamer

# Each Streamer implementation should be imported here.
from .streammagic import StreamMagic

# Map UPnP device models to Streamer implementations. This is not required if
# the UPnP model name is the same as the implementation name.
model_to_streamer = {
    "AXN10": StreamMagic,
    "CXN100": StreamMagic,
    "CXNv2": StreamMagic,
    "Edge NQ": StreamMagic,
    "Evo 150": StreamMagic,
    "Evo 75": StreamMagic,
    "Evo ONE": StreamMagic,
    "EXN100": StreamMagic,
    "MXN10": StreamMagic,
}
