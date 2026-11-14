"""Chargewise error envelope.

Every non-2xx response the API emits uses this shape. See
``docs/api-contracts.md`` section 1.1 for the full codes table.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raised anywhere in the app to produce a normalized JSON error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details

    def to_envelope(self) -> ErrorEnvelope:
        return ErrorEnvelope(
            error=ErrorBody(code=self.code, message=self.message, details=self.details)
        )


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details)
    ).model_dump(exclude_none=True)


def register_error_handlers(app: FastAPI) -> None:
    """Register handlers that coerce every error into the Chargewise envelope."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_envelope(
            exc.code, exc.message, exc.details
        ))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,  # HTTP_422_UNPROCESSABLE_ENTITY is deprecated in Starlette
            content=_envelope(
                "VALIDATION_ERROR",
                "Request body failed validation.",
                # ``ctx`` can hold the raised exception (custom validators), which
                # isn't JSON-serializable; render those as strings.
                {"errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
        }
        code = code_map.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, message))

    @app.exception_handler(Exception)
    async def _handle_unhandled(_: Request, exc: Exception) -> JSONResponse:
        # NOTE: do not leak exception details to the client in production. Sentry
        # (wired in Phase 6) will capture the full stack trace server-side.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("INTERNAL_ERROR", "An unexpected error occurred."),
        )


__all__ = ["ApiError", "ErrorBody", "ErrorEnvelope", "register_error_handlers"]
