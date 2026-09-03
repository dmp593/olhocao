import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context = ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for container logs without a runtime dependency."""

    extra_fields = (
        "booking_id",
        "entitlement_id",
        "cycle_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
    )

    def format(self, record):
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        for field in self.extra_fields:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
