from .amplifier import Amplifier

# Each Amplifier implementation should be imported here.
from .hegel import Hegel
from .streammagic import StreamMagic

# Map UPnP device models to Amplifier implementations. This is not required if
# the UPnP model name is the same as the implementation name.
#
# Note: Not all of these have been confirmed.
model_to_amplifier = {
    # Hegel
    "H95": Hegel,
    "H120": Hegel,
    "H190": Hegel,
    "H390": Hegel,
    "H590": Hegel,
    "H600": Hegel,
    # Cambridge Audio StreamMagic (streamers with preamp mode)
    "851N": StreamMagic,
    "AXN10": StreamMagic,
    "CXN100": StreamMagic,
    "CXNv2": StreamMagic,
    "Edge NQ": StreamMagic,
    "EXN100": StreamMagic,
    "MXN10": StreamMagic,
    # Cambridge Audio StreamMagic (streaming amplifiers)
    "Evo 75": StreamMagic,
    "Evo 150": StreamMagic,
    "Evo 150 SE": StreamMagic,
    "Evo ONE": StreamMagic,
}
