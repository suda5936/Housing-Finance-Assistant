import logging
import re
from collections.abc import Mapping, Sequence

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "account_number",
    "access_token",
    "api_key",
    "bank_account",
    "cookie",
    "document_text",
    "password",
    "prompt",
    "resident_registration_number",
    "rrn",
    "session_token",
    "user_prompt",
    "x-session-token",
}
RRN_PATTERN = re.compile(r"\b\d{6}-?[1-4]\d{6}\b")
ACCOUNT_PATTERN = re.compile(r"\b\d{2,6}-\d{2,6}-\d{2,6}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def redact_text(value: str) -> str:
    redacted = RRN_PATTERN.sub(REDACTED, value)
    redacted = ACCOUNT_PATTERN.sub(REDACTED, redacted)
    redacted = PHONE_PATTERN.sub(REDACTED, redacted)
    return EMAIL_PATTERN.sub(REDACTED, redacted)


def redact_sensitive(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if str(key).lower() in SENSITIVE_KEYS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return [redact_sensitive(item) for item in value]
    return value


class SensitiveDataLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, Mapping):
            record.msg = redact_sensitive(record.msg)
        else:
            record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def configure_privacy_logging() -> None:
    privacy_filter = SensitiveDataLogFilter()
    loggers = [
        logging.getLogger(),
        logging.getLogger("homefit_api"),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
    ]
    seen_handlers: set[int] = set()
    for logger in loggers:
        for handler in logger.handlers:
            if id(handler) in seen_handlers:
                continue
            handler.addFilter(privacy_filter)
            seen_handlers.add(id(handler))
