"""Errores con mensajes comprensibles para el usuario."""

from __future__ import annotations


class NoteCascadeError(Exception):
    """Error que puede mostrarse directamente al usuario.

    ``message`` explica qué sucedió y ``suggestion`` qué puede hacer a
    continuación. ``details`` guarda información técnica para el registro.
    """

    def __init__(self, message: str, suggestion: str = "", details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.details = details

    def user_text(self) -> str:
        text = self.message
        if self.suggestion:
            text += "\n\n" + self.suggestion
        return text


class UnsupportedFileError(NoteCascadeError):
    pass


class CorruptFileError(NoteCascadeError):
    pass


class EngineUnavailableError(NoteCascadeError):
    pass


class EngineFailedError(NoteCascadeError):
    pass


class CancelledError(NoteCascadeError):
    def __init__(self) -> None:
        super().__init__("La operación se canceló.")


class ExportError(NoteCascadeError):
    pass
