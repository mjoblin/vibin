class VibinError(Exception):
    pass


class VibinDeviceError(VibinError):
    pass


class VibinNotFoundError(VibinError):
    pass


class VibinMissingDependencyError(VibinError):
    pass
