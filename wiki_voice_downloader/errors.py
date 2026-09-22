"""Public errors deliberately contain no raw HTTP exception or credentials."""

class AppError(Exception):
    """An actionable error which may be shown to the user."""

class ConfigError(AppError):
    pass

class StorageError(AppError):
    pass

class SelectionError(AppError):
    pass

class ParseError(AppError):
    def __init__(self, message: str, reason: str = "structure_changed"):
        super().__init__(message)
        self.reason = reason

class NetworkError(AppError):
    def __init__(self, message: str, *, retryable: bool = False,
                 stop_site: bool = False, attempts: int = 0,
                 status_code: int | None = None, reason: str = "network_error"):
        super().__init__(message)
        self.retryable = retryable
        self.stop_site = stop_site
        self.attempts = attempts
        self.status_code = status_code
        self.reason = "access_restricted" if stop_site and reason == "network_error" else reason

class InvalidMedia(NetworkError):
    """A response is not supported, structurally complete audio data."""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('reason', 'source_format_changed' if message.startswith('source_format_changed') else 'invalid_media')
        super().__init__(message, **kwargs)

class UnsupportedMedia(InvalidMedia):
    """A format outside the supported subset, not necessarily corrupt."""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('reason', 'unsupported_media_format')
        super().__init__(message, **kwargs)


class AccessError(InvalidMedia):
    """An evidence-bearing stop, propagated unchanged through all layers."""
    def __init__(self, decision):
        super().__init__(decision.diagnostic_message, stop_site=True, retryable=False,
                         status_code=decision.http_status, reason=decision.kind)
        self.decision = decision


class SnapshotError(ParseError):
    """Local input failure with a stable code and no absolute input path."""
    def __init__(self, message, reason='invalid_html_snapshot'):
        super().__init__(f'{reason}: {message}', reason)
