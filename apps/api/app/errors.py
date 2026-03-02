from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorType(StrEnum):
    TRANSIENT = "transient"
    FATAL = "fatal"


class ApiContractError(Exception):
    """Typed API error aligned with frontend error contract expectations."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        error_type: ErrorType,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.error_type = error_type

    @property
    def retryable(self) -> bool:
        return self.error_type is ErrorType.TRANSIENT

    def to_response(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "error_type": self.error_type.value,
            "retryable": self.retryable,
        }
