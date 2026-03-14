"""Project-specific exceptions."""

from __future__ import annotations


class TweetXVaultError(RuntimeError):
    """Base exception for the project."""


class ConfigError(TweetXVaultError):
    """Raised for local configuration issues."""


class AuthResolutionError(ConfigError):
    """Raised when local auth inputs cannot be resolved."""


class QueryIdRefreshError(TweetXVaultError):
    """Raised when query IDs cannot be refreshed or resolved."""


class APIResponseError(TweetXVaultError):
    """Raised when X returns an unexpected API response."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class AuthExpiredError(APIResponseError):
    """Raised for expired or rejected credentials."""


class FeatureFlagDriftError(APIResponseError):
    """Raised when a request likely fails because feature flags drifted."""


class RateLimitExhaustedError(APIResponseError):
    """Raised when retry and cooldown logic still ends in 429."""


class StaleQueryIdError(APIResponseError):
    """Raised when a query ID refresh still cannot satisfy an operation."""


class ProcessLockError(TweetXVaultError):
    """Raised when another sync already holds the process lock."""


class ArchiveOwnerMismatchError(TweetXVaultError):
    """Raised when a local archive owner differs from the resolved X account."""
