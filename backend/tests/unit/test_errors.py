"""Every error code is catalogued and round-trips through to_response()."""
from __future__ import annotations

import json

from backend.errors import (
    ALL_ERRORS,
    AuthExpired,
    AuthRequired,
    BrowserError,
    BulkFetchTooLarge,
    CloudUnreachable,
    ErrorCode,
    NotFound,
    QueryInvalidNegation,
    QueryTimeout,
    Recovery,
    ValidationFailed,
)


def test_all_errors_have_distinct_codes() -> None:
    codes = {cls.code for cls in ALL_ERRORS}
    assert len(codes) == len(ALL_ERRORS)


def test_every_error_code_enum_has_a_class() -> None:
    class_codes = {cls.code for cls in ALL_ERRORS}
    enum_codes = set(ErrorCode)
    assert class_codes == enum_codes, f"missing classes for: {enum_codes - class_codes}"


def test_every_error_serializes_to_expected_shape() -> None:
    for cls in ALL_ERRORS:
        err = cls()
        body = err.to_response(request_id="abc123")
        assert "error" in body
        e = body["error"]
        assert e["code"] == err.code.value
        assert e["message"] == err.final_message
        assert e["recovery"] in {r.value for r in Recovery}
        assert e["requestId"] == "abc123"
        # JSON-serializable.
        json.dumps(body)


def test_override_message_applies() -> None:
    err = NotFound("Dataset xyz not found")
    assert err.final_message == "Dataset xyz not found"
    body = err.to_response()
    assert body["error"]["message"] == "Dataset xyz not found"


def test_details_appear_in_response() -> None:
    err = BulkFetchTooLarge(details={"max": 500, "requested": 501})
    body = err.to_response()
    assert body["error"]["details"] == {"max": 500, "requested": 501}


def test_http_status_correctness() -> None:
    assert AuthRequired().http_status == 401
    assert AuthExpired().http_status == 401
    assert NotFound().http_status == 404
    assert CloudUnreachable().http_status == 502
    assert QueryTimeout().http_status == 504
    assert QueryInvalidNegation().http_status == 400


def test_isinstance_checks() -> None:
    for cls in ALL_ERRORS:
        assert issubclass(cls, BrowserError)
        assert cls().code in set(ErrorCode)
