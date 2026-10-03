"""Exception hierarchy for instaDM."""


class InstaDMError(Exception):
    """Base exception for all instaDM errors."""


class AuthError(InstaDMError):
    """Session cookies are missing, invalid or expired."""


class ChallengeRequired(AuthError):
    """Instagram is demanding a login challenge / checkpoint for this account."""


class RateLimited(InstaDMError):
    """HTTP 429 from Instagram. ``retry_after`` is seconds, when known."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotFoundError(InstaDMError):
    """Thread / item / user does not exist (or is not visible to this account)."""


class SendError(InstaDMError):
    """A send/reply action failed."""


class RealtimeError(InstaDMError):
    """The realtime (MQTT) connection failed in a non-recoverable way."""


class ProtocolError(RealtimeError):
    """Malformed MQTT/iris data from the broker."""
