"""Errors raised while converting external data into tariff models."""


class ParseError(ValueError):
    """A parsing failure annotated with the JSON path of the invalid value."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")
