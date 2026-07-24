import logging

from homefit_api.privacy import REDACTED, SensitiveDataLogFilter, redact_sensitive


def test_redact_sensitive_masks_keys_and_identifiers() -> None:
    value = {
        "name": "합성 사용자",
        "rrn": "990101-1234567",
        "nested": {"account_number": "123-456-789012"},
        "contact": "010-1234-5678 user@example.com",
        "user_prompt": "private document content",
    }

    redacted = redact_sensitive(value)

    assert redacted == {
        "name": "합성 사용자",
        "rrn": REDACTED,
        "nested": {"account_number": REDACTED},
        "contact": f"{REDACTED} {REDACTED}",
        "user_prompt": REDACTED,
    }


def test_log_filter_masks_identifier() -> None:
    record = logging.LogRecord(
        name="homefit_api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="resident=990101-1234567",
        args=(),
        exc_info=None,
    )

    assert SensitiveDataLogFilter().filter(record) is True
    assert record.getMessage() == f"resident={REDACTED}"


def test_log_filter_masks_sensitive_mapping_keys() -> None:
    record = logging.LogRecord(
        name="homefit_api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg={"access_token": "secret", "event": "session_created"},
        args=(),
        exc_info=None,
    )

    assert SensitiveDataLogFilter().filter(record) is True
    assert record.msg == {"access_token": REDACTED, "event": "session_created"}
