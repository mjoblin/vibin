class VibinError(Exception):
    pass


class VibinDeviceError(VibinError):
    """A media hardware device issue."""
    pass


class VibinInputError(VibinError):
    """Bad/unexpected user input."""
    pass


class VibinNotFoundError(VibinError):
    """Something was not found."""
    pass


class VibinMissingDependencyError(VibinError):
    """A required dependency is unavailable."""
    pass
