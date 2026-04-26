"""Trust-proxy config regression test (B8).

The audit (synthesis §B8) flagged that uvicorn was invoked without
`--proxy-headers --forwarded-allow-ips '*'`. That meant
`request.client.host` resolved to Railway's edge-proxy IP for every
request, not the real client IP — silently breaking the per-IP
rate-limit envelope:

- One bad actor could DoS *every* user's login (5 attempts per IP per
  15 min, all sharing the proxy IP).
- Distributed attacks looked single-source in metrics and logs.

The fix lives in the start command. There's nothing to assert at the
ASGI layer because uvicorn rewrites `request.client.host` from
X-Forwarded-For *before* the ASGI scope reaches FastAPI — by the time
test client code can look at `request.client`, the rewrite has either
happened or it hasn't. So we pin the regression at the config-file
layer instead: both the Dockerfile CMD and the Railway start command
must carry `--proxy-headers --forwarded-allow-ips`. If either drifts
back, this test fails before the bad config ships.

The `'*'` wildcard for `--forwarded-allow-ips` is intentional: Railway
does not publish a stable list of edge-proxy IPs, and the underlying
container is not directly internet-reachable (Railway's networking
only exposes the edge URL). If that ever changes, the wildcard becomes
unsafe and must be tightened.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_cmd_carries_proxy_headers_flags() -> None:
    """`infra/Dockerfile` is the fallback CMD. Even if Railway's startCommand
    is unset, the container itself must still trust X-Forwarded-For from the
    Railway edge proxy."""
    dockerfile = (REPO_ROOT / "infra" / "Dockerfile").read_text()
    assert "--proxy-headers" in dockerfile, (
        "infra/Dockerfile CMD must include --proxy-headers (B8). Without it, "
        "request.client.host resolves to the Railway edge IP for every "
        "request and per-IP rate limits collapse onto a single subject."
    )
    assert "--forwarded-allow-ips" in dockerfile, (
        "infra/Dockerfile CMD must include --forwarded-allow-ips (B8). "
        "Without an explicit allowlist, uvicorn ignores X-Forwarded-For "
        "regardless of --proxy-headers."
    )


def test_railway_toml_start_command_carries_proxy_headers_flags() -> None:
    """`infra/railway.toml` startCommand wins on Railway when set. Belt and
    suspenders: keep both files aligned so a Railway operator can redeploy
    via either path without losing the flag."""
    railway_toml = (REPO_ROOT / "infra" / "railway.toml").read_text()
    assert "--proxy-headers" in railway_toml, (
        "infra/railway.toml startCommand must include --proxy-headers (B8)."
    )
    assert "--forwarded-allow-ips" in railway_toml, (
        "infra/railway.toml startCommand must include --forwarded-allow-ips (B8)."
    )
