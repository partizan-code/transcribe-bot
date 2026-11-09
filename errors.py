"""Safe, user-facing errors. Never include upstream exception messages."""


class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
