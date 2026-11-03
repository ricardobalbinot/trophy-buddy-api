"""Controlled application failures; provider exceptions must not cross this boundary."""


class ApplicationError(Exception):
    """Expose a stable error code and HTTP status without private provider details."""

    def __init__(self, code: str, status: int = 503) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class Busy(ApplicationError):
    def __init__(self) -> None:
        super().__init__("operation_busy", 429)


class NotFound(ApplicationError):
    def __init__(self) -> None:
        super().__init__("game_not_found", 404)


class ReconnectRequired(ApplicationError):
    def __init__(self) -> None:
        super().__init__("psn_reconnect_required", 503)
