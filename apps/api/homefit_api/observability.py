import logging
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from homefit_api.privacy import redact_sensitive

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
logger = logging.getLogger("homefit_api.requests")


class RouteMetric(BaseModel):
    route: str
    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    average_duration_ms: float = Field(ge=0)
    maximum_duration_ms: int = Field(ge=0)


class MetricsSnapshot(BaseModel):
    total_requests: int = Field(ge=0)
    total_errors: int = Field(ge=0)
    routes: list[RouteMetric]


@dataclass(slots=True)
class _MutableMetric:
    requests: int = 0
    errors: int = 0
    total_duration_ms: int = 0
    maximum_duration_ms: int = 0


class OperationalMetrics:
    """Thread-safe, process-local aggregate metrics without user payloads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, _MutableMetric] = defaultdict(_MutableMetric)

    def record(self, route: str, status_code: int, duration_ms: int) -> None:
        with self._lock:
            metric = self._routes[route]
            metric.requests += 1
            metric.errors += int(status_code >= 400)
            metric.total_duration_ms += duration_ms
            metric.maximum_duration_ms = max(metric.maximum_duration_ms, duration_ms)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            routes = [
                RouteMetric(
                    route=route,
                    requests=metric.requests,
                    errors=metric.errors,
                    average_duration_ms=round(
                        metric.total_duration_ms / metric.requests, 2
                    ),
                    maximum_duration_ms=metric.maximum_duration_ms,
                )
                for route, metric in sorted(self._routes.items())
                if metric.requests
            ]
        return MetricsSnapshot(
            total_requests=sum(item.requests for item in routes),
            total_errors=sum(item.errors for item in routes),
            routes=routes,
        )

    def reset(self) -> None:
        with self._lock:
            self._routes.clear()


metrics = OperationalMetrics()
router = APIRouter(prefix="/system", tags=["system"])


def _request_id(value: str | None) -> str:
    if value and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def _route_name(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else request.url.path


def _security_headers(response: Response, *, use_hsts: bool) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if use_hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


class RequestSafetyMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        max_request_bytes: int,
        use_hsts: bool,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_request_bytes = max_request_bytes
        self._use_hsts = use_hsts

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started = time.monotonic()
        content_length = request.headers.get("content-length")
        response: Response
        if content_length and content_length.isdecimal():
            if int(content_length) > self._max_request_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "요청 크기가 허용 한도를 초과했습니다.",
                            "fields": [],
                            "request_id": request_id,
                        }
                    },
                )
                return self._finish(request, response, request_id, started)

        response = await call_next(request)
        return self._finish(request, response, request_id, started)

    def _finish(
        self,
        request: Request,
        response: Response,
        request_id: str,
        started: float,
    ) -> Response:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        route = _route_name(request)
        metrics.record(route, response.status_code, duration_ms)
        response.headers["X-Request-ID"] = request_id
        _security_headers(response, use_hsts=self._use_hsts)
        if route.startswith("/sessions"):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            redact_sensitive(
                {
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
        )
        return response


@router.get("/metrics", response_model=MetricsSnapshot)
def operational_metrics() -> MetricsSnapshot:
    """Return low-cardinality aggregates without request bodies or identifiers."""

    return metrics.snapshot()
