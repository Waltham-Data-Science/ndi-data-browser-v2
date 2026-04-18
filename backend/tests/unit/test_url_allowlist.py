"""Host allowlist helpers for download_file Bearer-forwarding (PR-6)."""
from __future__ import annotations

from urllib.parse import urlparse

from backend.clients._url_allowlist import (
    build_runtime_allowlist,
    extract_host,
    host_matches_allowlist,
    url_pattern_for_log,
)
from backend.config import get_settings

_DEFAULT_ALLOWLIST = [
    "s3.amazonaws.com",
    "*.s3.amazonaws.com",
    "*.s3.us-east-1.amazonaws.com",
    "*.s3.us-east-2.amazonaws.com",
    "*.s3.us-west-1.amazonaws.com",
    "*.s3.us-west-2.amazonaws.com",
    "*.cloudfront.net",
]


def test_host_matches_exact_entry() -> None:
    assert host_matches_allowlist("s3.amazonaws.com", _DEFAULT_ALLOWLIST) is True


def test_host_matches_wildcard_suffix() -> None:
    assert host_matches_allowlist(
        "mybucket.s3.amazonaws.com", _DEFAULT_ALLOWLIST,
    ) is True
    assert host_matches_allowlist(
        "deep.nested.mybucket.s3.amazonaws.com", _DEFAULT_ALLOWLIST,
    ) is True


def test_host_matches_regional_wildcard() -> None:
    assert host_matches_allowlist(
        "bucket.s3.us-east-1.amazonaws.com", _DEFAULT_ALLOWLIST,
    ) is True
    assert host_matches_allowlist(
        "bucket.s3.us-west-2.amazonaws.com", _DEFAULT_ALLOWLIST,
    ) is True


def test_host_matches_cloudfront_wildcard() -> None:
    assert host_matches_allowlist(
        "d111111abcdef8.cloudfront.net", _DEFAULT_ALLOWLIST,
    ) is True


def test_host_does_not_match_foreign_domain() -> None:
    assert host_matches_allowlist("evil.com", _DEFAULT_ALLOWLIST) is False
    assert host_matches_allowlist("example.org", _DEFAULT_ALLOWLIST) is False
    # A host that only *contains* an allowlist substring must not match —
    # guards against "evil-s3.amazonaws.com.attacker.io" style bypasses.
    assert host_matches_allowlist(
        "evils3.amazonaws.com.attacker.io", _DEFAULT_ALLOWLIST,
    ) is False


def test_host_match_is_case_insensitive() -> None:
    assert host_matches_allowlist(
        "MyBucket.S3.AMAZONAWS.COM", _DEFAULT_ALLOWLIST,
    ) is True


def test_wildcard_matches_bare_suffix() -> None:
    # `*.cloudfront.net` should also match `cloudfront.net` (the bare apex).
    assert host_matches_allowlist("cloudfront.net", ["*.cloudfront.net"]) is True


def test_empty_allowlist_rejects_everything() -> None:
    assert host_matches_allowlist("s3.amazonaws.com", []) is False


def test_empty_host_does_not_match() -> None:
    assert host_matches_allowlist("", _DEFAULT_ALLOWLIST) is False


def test_extract_host_strips_port_and_lowercases() -> None:
    assert extract_host("https://S3.Amazonaws.com:443/foo") == "s3.amazonaws.com"


def test_extract_host_handles_malformed() -> None:
    assert extract_host("not a url") == ""


def test_url_pattern_for_log_strips_query() -> None:
    signed = (
        "https://mybucket.s3.amazonaws.com/path/file.nbf"
        "?X-Amz-Signature=SECRET&X-Amz-Credential=xyz"
    )
    assert url_pattern_for_log(signed) == (
        "https://mybucket.s3.amazonaws.com/path/file.nbf"
    )


def test_url_pattern_for_log_handles_no_host() -> None:
    assert url_pattern_for_log("") == "<no-host>"


def test_build_runtime_allowlist_includes_cloud_host() -> None:
    composed = build_runtime_allowlist(
        _DEFAULT_ALLOWLIST, "https://api.example.test/v1",
    )
    assert "api.example.test" in composed
    # Original entries preserved.
    assert "s3.amazonaws.com" in composed
    assert "*.cloudfront.net" in composed


def test_host_matches_cloud_base_url() -> None:
    """Runtime allowlist always contains the cloud host, so urls pointed at the
    cloud always match — even when the static allowlist is empty."""
    settings = get_settings()
    cloud_host = urlparse(settings.cloud_base_url).hostname or ""
    assert cloud_host  # sanity

    runtime = build_runtime_allowlist([], settings.cloud_base_url)
    assert host_matches_allowlist(cloud_host, runtime) is True
