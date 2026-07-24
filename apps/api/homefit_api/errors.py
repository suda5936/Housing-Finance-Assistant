from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "fields": fields or [],
                "request_id": request_id,
            }
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "reason": str(item["msg"]),
            }
            for item in error.errors()
        ]
        return _error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="입력값을 확인해 주세요.",
            fields=fields,
            request_id=getattr(request.state, "request_id", None),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        code_by_status = {
            400: "BAD_REQUEST",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
        }
        detail: Any = error.detail
        message = detail if isinstance(detail, str) else "요청을 처리할 수 없습니다."
        return _error_response(
            status_code=error.status_code,
            code=code_by_status.get(error.status_code, "HTTP_ERROR"),
            message=message,
            request_id=getattr(request.state, "request_id", None),
        )
