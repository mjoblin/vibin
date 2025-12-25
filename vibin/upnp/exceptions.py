"""UPnP-related exceptions for vibin.

These exceptions provide a library-agnostic way to handle UPnP errors,
allowing vibin to work with either upnpclient or async_upnp_client.
"""


class VibinUpnpError(Exception):
    """Base exception for UPnP-related errors."""

    pass


class VibinSoapError(VibinUpnpError):
    """Exception for SOAP action errors.

    This wraps SOAP errors from either UPnP library, providing a consistent
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
