"""Exceptions exposed by the offline builder."""


class BuildError(RuntimeError):
    """Raised when a complete offline site cannot be produced safely."""


class BuildBlocked(BuildError):
    """Raised when a filesystem lock prevents an atomic site patch."""
