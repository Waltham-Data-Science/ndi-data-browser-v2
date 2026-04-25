"""Tests for the cookie_attrs helper.

The helper centralizes the per-environment Set-Cookie/Delete-Cookie
attribute derivation that the backend uses for the session and CSRF
cookies. Production cookies must carry ``Domain=.ndi-cloud.com`` so the
cross-repo Vercel frontend (Phase 4 of the cross-repo unification) can
read them; dev cookies must not carry ``Secure`` because local dev
serves over plain HTTP; everything else (staging) is secure but
host-only.
"""
from typing import Literal

from backend.auth.cookie_attrs import cookie_attrs
from backend.config import Settings

EnvName = Literal["development", "staging", "production"]


def _settings(env: EnvName) -> Settings:
    # Other required fields (NDI_CLOUD_URL, REDIS_URL, etc.) come from
    # the env vars set in backend/tests/conftest.py.
    return Settings(ENVIRONMENT=env)


def test_production_returns_secure_with_apex_domain() -> None:
    assert cookie_attrs(_settings("production")) == {
        "secure": True,
        "domain": ".ndi-cloud.com",
    }


def test_development_returns_insecure_without_domain() -> None:
    attrs = cookie_attrs(_settings("development"))
    assert attrs == {"secure": False}
    assert "domain" not in attrs


def test_staging_returns_secure_without_domain() -> None:
    attrs = cookie_attrs(_settings("staging"))
    assert attrs == {"secure": True}
    assert "domain" not in attrs
