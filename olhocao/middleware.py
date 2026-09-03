import logging
import re
import time
from uuid import uuid4

from olhocao.logging import request_id_context

logger = logging.getLogger("olhocao.request")
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Add a safe correlation id to every request, response and application log."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming_id = request.headers.get("X-Request-ID", "")
        request_id = incoming_id if VALID_REQUEST_ID.fullmatch(incoming_id) else uuid4().hex
        request.request_id = request_id
        token = request_id_context.set(request_id)
        started_at = time.monotonic()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_context.reset(token)


class BrowserSecurityHeadersMiddleware:
    """Attach browser capability restrictions maintained by the application."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        return response
