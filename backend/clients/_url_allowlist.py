"""Host allowlist checks for Bearer-token forwarding on binary downloads.

The cloud document payload embeds signed file URLs that a malicious (or compromised)
dataset author could point at an attacker-controlled host. When
`NdiCloudClient.download_file()` forwards the user's Cognito Bearer to such a URL,
the attacker captures the token. This module gates that forwarding by host.

Matching rules:
- Exact entry `"s3.amazonaws.com"` matches host `"s3.amazonaws.com"` only.
- Wildcard entry `"*.s3.amazonaws.com"` matches both `"s3.amazonaws.com"`
  (the suffix itself) and any subdomain `"mybucket.s3.amazonaws.com"`.
- All matches are exact string comparisons on lowercased hosts.

The runtime allowlist = configured static allowlist + the cloud host (which is
always implicitly trusted). See PR-6 / Phase 2 Code #4.
"""
from __future__ import annotations

from urllib.parse import urlparse


def host_matches_allowlist(host: str, allowlist: list[str]) -> bool:
    """Host matches if equal to an exact entry or matches a `*.x` wildcard entry.

    A wildcard entry `*.example.com` matches both `example.com` (the bare suffix)
    and `sub.example.com` (any subdomain). Comparisons are case-insensitive.
    """
    h = host.lower()
    for pattern in allowlist:
        p = pattern.strip().lower()
        if not p:
            continue
        if p.startswith("*."):
            suffix = p[1:]  # e.g. ".s3.amazonaws.com"
            bare = suffix[1:]  # e.g. "s3.amazonaws.com"
            if h == bare or h.endswith(suffix):
                return True
        elif h == p:
            return True
    return False


def extract_host(url: str) -> str:
    """Return the lowercased host (netloc without port) for an absolute URL.

    Returns empty string if parsing fails or the URL has no host.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    netloc = parsed.hostname or ""
    return netloc.lower()


def build_runtime_allowlist(static_allowlist: list[str], cloud_base_url: str) -> list[str]:
    """Compose the effective allowlist = configured static + cloud host."""
    cloud_host = extract_host(cloud_base_url)
    if not cloud_host:
        return list(static_allowlist)
    # Preserve ordering of the configured entries; cloud host appended last.
    return [*static_allowlist, cloud_host]


def url_pattern_for_log(url: str) -> str:
    """Return a log-safe representation of a URL.

    Drops query string (which may contain signed-URL secrets) and only keeps
    scheme://host/path. Used in `cloud.download.off_allowlist_host` warnings
    where we want to observe destinations without leaking credentials.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "<unparseable-url>"
    scheme = parsed.scheme or ""
    host = parsed.hostname or ""
    path = parsed.path or ""
    if not host:
        return "<no-host>"
    return f"{scheme}://{host}{path}"
