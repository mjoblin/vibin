from .amplifier import Amplifier

# Each Amplifier implementation should be imported here.
from .hegel import Hegel

# Map UPnP device models to Amplifier implementations. This is not required if
# the UPnP model name is the same as the implementation name.
model_to_amplifier = {
    "H95": Hegel,
    "H120": Hegel,
    "H190": Hegel,
    "H390": Hegel,
    "H590": Hegel,
    "H600": Hegel,
}
