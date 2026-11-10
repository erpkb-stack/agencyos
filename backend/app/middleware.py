"""Request context (request ID, timing, structured access log) and security headers."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("agencyos.request")
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'"
)
# Swagger UI loads its assets from a CDN and uses inline scripts.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_request_bytes: int = 5 * 1024 * 1024, hsts: bool = False) -> None:
        super().__init__(app)
        self.max_request_bytes = max_request_bytes
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        # Reject oversized bodies before anything parses them (multipart uploads included).
        declared = request.headers.get("content-length")
        too_big = declared is not None and (not declared.isdigit() or int(declared) > self.max_request_bytes)
        chunked = "chunked" in request.headers.get("transfer-encoding", "").lower()
        if too_big or (chunked and declared is None):
            from starlette.responses import JSONResponse

            status_code = 413 if too_big else 411
            message = "Request body is too large" if too_big else "Content-Length is required"
            return JSONResponse({"error": {"code": "payload_too_large" if too_big else "length_required",
                                           "message": message, "details": None}, "request_id": request_id},
                                status_code=status_code, headers={"X-Request-ID": request_id})

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        response.headers["X-Request-ID"] = request_id
        path = request.url.path
        is_docs = path in ("/docs", "/redoc") or path.startswith("/docs/")
        # Routes may set a stricter or route-specific policy (e.g. sandboxed landing-page previews).
        response.headers.setdefault("Content-Security-Policy", DOCS_CSP if is_docs else CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if self.hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/assets/") or path in ("/", "/login"):
            # Revalidate with the ETag on every load (a cheap 304 when unchanged) so an upgrade is
            # picked up immediately instead of the browser running stale scripts.
            response.headers.setdefault("Cache-Control", "no-cache")

        if path.startswith("/api/"):
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "user_id": getattr(request.state, "user_id", None),
                },
            )
        return response
