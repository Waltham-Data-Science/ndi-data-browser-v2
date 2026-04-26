"""Typed error hierarchy.

Every error the system raises inherits from BrowserError and carries a stable
error code. A single FastAPI exception handler serializes them into the shape
documented in docs/error-catalog.md. Raw Exceptions never reach the wire.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_RATE_LIMITED = "AUTH_RATE_LIMITED"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    EMAIL_ALREADY_VERIFIED = "EMAIL_ALREADY_VERIFIED"
    WEAK_PASSWORD = "WEAK_PASSWORD"
    INVALID_VERIFICATION_CODE = "INVALID_VERIFICATION_CODE"
    VERIFICATION_CODE_EXPIRED = "VERIFICATION_CODE_EXPIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    CLOUD_UNREACHABLE = "CLOUD_UNREACHABLE"
    CLOUD_TIMEOUT = "CLOUD_TIMEOUT"
    CLOUD_INTERNAL_ERROR = "CLOUD_INTERNAL_ERROR"
    BINARY_DECODE_FAILED = "BINARY_DECODE_FAILED"
    BINARY_NOT_FOUND = "BINARY_NOT_FOUND"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_TOO_LARGE = "QUERY_TOO_LARGE"
    QUERY_INVALID_NEGATION = "QUERY_INVALID_NEGATION"
    BULK_FETCH_TOO_LARGE = "BULK_FETCH_TOO_LARGE"
    ONTOLOGY_LOOKUP_FAILED = "ONTOLOGY_LOOKUP_FAILED"
    CSRF_INVALID = "CSRF_INVALID"
    INTERNAL = "INTERNAL"


class Recovery(str, Enum):
    RETRY = "retry"
    LOGIN = "login"
    CONTACT_SUPPORT = "contact_support"
    NONE = "none"


class BrowserError(Exception):
    """Base class. Subclasses pin code/status/recovery; instance supplies message + context."""

    code: ErrorCode = ErrorCode.INTERNAL
    http_status: int = 500
    user_message: str = "Something went wrong. We've been notified."
    recovery: Recovery = Recovery.CONTACT_SUPPORT

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        self.user_message_override = message
        self.details = details
        self.log_context = log_context or {}
        super().__init__(self.user_message_override or self.user_message)

    @property
    def final_message(self) -> str:
        return self.user_message_override or self.user_message

    def to_response(self, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {
                "code": self.code.value,
                "message": self.final_message,
                "recovery": self.recovery.value,
                "requestId": request_id,
            }
        }
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


# --- Auth errors ---

class AuthRequired(BrowserError):
    code = ErrorCode.AUTH_REQUIRED
    http_status = 401
    user_message = "Please log in to view this content."
    recovery = Recovery.LOGIN


class AuthExpired(BrowserError):
    code = ErrorCode.AUTH_EXPIRED
    http_status = 401
    user_message = "Your session has expired. Please log in again."
    recovery = Recovery.LOGIN


class AuthInvalidCredentials(BrowserError):
    code = ErrorCode.AUTH_INVALID_CREDENTIALS
    http_status = 401
    user_message = "Invalid username or password."
    recovery = Recovery.NONE


class AuthRateLimited(BrowserError):
    code = ErrorCode.AUTH_RATE_LIMITED
    http_status = 429
    user_message = "Too many login attempts. Please wait a few minutes."
    recovery = Recovery.NONE


# --- Account-lifecycle errors ---
# Cognito's raw error codes (UsernameExistsException, InvalidPasswordException,
# CodeMismatchException, etc.) are deliberately translated to typed
# BrowserError subclasses in NdiCloudClient. The wire never sees Cognito
# strings — see auth-proxy endpoints in routers/auth.py and the
# `_translate_cognito_signup_error` / `_translate_cognito_*` helpers.

class EmailAlreadyExists(BrowserError):
    code = ErrorCode.EMAIL_ALREADY_EXISTS
    # 409 Conflict — the resource (user account for this email) already exists.
    http_status = 409
    user_message = "An account with this email already exists. Try logging in instead."
    recovery = Recovery.LOGIN


class EmailAlreadyVerified(BrowserError):
    code = ErrorCode.EMAIL_ALREADY_VERIFIED
    # 409 Conflict — caller asked to (re)verify a verified address.
    http_status = 409
    user_message = "This email is already verified. Please log in."
    recovery = Recovery.LOGIN


class WeakPassword(BrowserError):
    code = ErrorCode.WEAK_PASSWORD
    http_status = 400
    user_message = (
        "Password doesn't meet complexity requirements. Use at least 8 characters "
        "with uppercase, lowercase, number, and symbol."
    )
    recovery = Recovery.NONE


class InvalidVerificationCode(BrowserError):
    code = ErrorCode.INVALID_VERIFICATION_CODE
    http_status = 400
    user_message = "The verification code is incorrect. Please check and try again."
    recovery = Recovery.NONE


class VerificationCodeExpired(BrowserError):
    code = ErrorCode.VERIFICATION_CODE_EXPIRED
    http_status = 400
    user_message = "The verification code has expired. Please request a new one."
    recovery = Recovery.NONE


class Forbidden(BrowserError):
    code = ErrorCode.FORBIDDEN
    http_status = 403
    user_message = "You don't have access to this resource."
    recovery = Recovery.NONE


class NotFound(BrowserError):
    code = ErrorCode.NOT_FOUND
    http_status = 404
    user_message = "This dataset or document doesn't exist or you can't access it."
    recovery = Recovery.NONE


class ValidationFailed(BrowserError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 400
    user_message = "Your request was invalid."
    recovery = Recovery.NONE


class RateLimited(BrowserError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429
    user_message = "You're making requests too quickly. Please wait a moment."
    recovery = Recovery.RETRY


# --- Cloud errors ---

class CloudUnreachable(BrowserError):
    code = ErrorCode.CLOUD_UNREACHABLE
    http_status = 502
    user_message = "NDI Cloud is temporarily unavailable. We'll retry automatically."
    recovery = Recovery.RETRY


class CloudTimeout(BrowserError):
    code = ErrorCode.CLOUD_TIMEOUT
    http_status = 504
    user_message = "The request took too long. Please try again."
    recovery = Recovery.RETRY


class CloudInternalError(BrowserError):
    code = ErrorCode.CLOUD_INTERNAL_ERROR
    http_status = 502
    user_message = "NDI Cloud returned an error. We've been notified."
    recovery = Recovery.RETRY


# --- Binary errors ---

class BinaryDecodeFailed(BrowserError):
    code = ErrorCode.BINARY_DECODE_FAILED
    http_status = 502
    user_message = "Could not read the binary data for this document."
    recovery = Recovery.CONTACT_SUPPORT


class BinaryNotFound(BrowserError):
    code = ErrorCode.BINARY_NOT_FOUND
    http_status = 404
    user_message = "The binary data for this document is not available."
    recovery = Recovery.NONE


# --- Query errors ---

class QueryTimeout(BrowserError):
    code = ErrorCode.QUERY_TIMEOUT
    http_status = 504
    user_message = "Your query took too long. Please narrow the scope or add an isa filter."
    recovery = Recovery.RETRY


class QueryTooLarge(BrowserError):
    code = ErrorCode.QUERY_TOO_LARGE
    http_status = 400
    user_message = "Your query matched too many documents. Please narrow your search."
    recovery = Recovery.NONE


class QueryInvalidNegation(BrowserError):
    code = ErrorCode.QUERY_INVALID_NEGATION
    http_status = 400
    user_message = "The `~or` operation isn't supported. Please restructure your query."
    recovery = Recovery.NONE


class BulkFetchTooLarge(BrowserError):
    code = ErrorCode.BULK_FETCH_TOO_LARGE
    http_status = 400
    user_message = "You can fetch at most 500 documents at a time."
    recovery = Recovery.NONE


# --- Ontology ---

class OntologyLookupFailed(BrowserError):
    code = ErrorCode.ONTOLOGY_LOOKUP_FAILED
    http_status = 502
    user_message = "Could not look up ontology term."
    recovery = Recovery.RETRY


# --- CSRF ---

class CsrfInvalid(BrowserError):
    code = ErrorCode.CSRF_INVALID
    http_status = 403
    user_message = "Your session is out of sync. Please refresh the page."
    recovery = Recovery.RETRY


# --- Fallback ---

class Internal(BrowserError):
    code = ErrorCode.INTERNAL
    http_status = 500
    user_message = "Something went wrong. We've been notified."
    recovery = Recovery.CONTACT_SUPPORT


ALL_ERRORS: list[type[BrowserError]] = [
    AuthRequired, AuthExpired, AuthInvalidCredentials, AuthRateLimited,
    EmailAlreadyExists, EmailAlreadyVerified, WeakPassword,
    InvalidVerificationCode, VerificationCodeExpired,
    Forbidden, NotFound, ValidationFailed, RateLimited,
    CloudUnreachable, CloudTimeout, CloudInternalError,
    BinaryDecodeFailed, BinaryNotFound,
    QueryTimeout, QueryTooLarge, QueryInvalidNegation, BulkFetchTooLarge,
    OntologyLookupFailed, CsrfInvalid, Internal,
]
