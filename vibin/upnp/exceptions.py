"""UPnP-related exceptions for vibin."""


class VibinSoapError(Exception):
    """Exception for SOAP action errors.

    This wraps SOAP errors from the UPnP library, providing a consistent
    interface for error handling.
    """

    def __init__(self, message: str, error_code: int | None = None):
        """Initialize the SOAP error.

        Args:
            message: Human-readable error description.
            error_code: Optional UPnP error code from the device response.
        """
        super().__init__(message)
        self.error_code = error_code
