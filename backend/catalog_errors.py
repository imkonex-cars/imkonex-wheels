"""Shared importer exceptions, identical for imported code and ``python -m``.

Keeping these outside the executable module prevents duplicate exception
classes when the entry point is __main__ and converters import catalog_sync.
"""


class SyncError(RuntimeError):
    """A fixed, public error identifier, never a provider exception message."""


class ProviderError(SyncError):
    pass


class AuthenticationError(SyncError):
    pass


class UnsupportedProduct(ValueError):
    """Unsupported descriptive parameters; does not cover prices or stock."""
