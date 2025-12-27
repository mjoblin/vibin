class VibinError(Exception):
    pass


class VibinDeviceError(VibinError):
    """A media hardware device issue."""

    pass


class VibinInputError(VibinError):
    """Bad/unexpected user input."""

    pass


class VibinMissingDependencyError(VibinError):
    """A required dependency is unavailable."""

    pass


class VibinMediaServerError(VibinError):
    """Media Server error (e.g. unreachable or timed out)."""

    pass


class VibinNotFoundError(VibinError):
    """Something was not found."""

    pass


class VibinSoapError(VibinError):
    """Exception for SOAP action errors."""

    def __init__(self, message: str, error_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code
