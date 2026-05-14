from backend.middleware.csrf import EXEMPT_PATHS, generate_token, sign, verify


def test_sign_verify_roundtrip() -> None:
    tok = generate_token()
    signed = sign(tok)
    assert verify(signed)


def test_tampered_token_fails() -> None:
    tok = generate_token()
    signed = sign(tok)
    parts = signed.split(".")
    # Flip last char of MAC.
    tampered = parts[0] + "." + parts[1][:-1] + ("0" if parts[1][-1] != "0" else "1")
    assert not verify(tampered)


def test_non_dot_format_fails() -> None:
    assert not verify("nodot")


def test_distinct_tokens_are_unique() -> None:
    a = generate_token()
    b = generate_token()
    assert a != b


def test_ontology_batch_lookup_is_csrf_exempt() -> None:
    """Anonymous /api/ontology/batch-lookup must work without a CSRF token.

    The endpoint is POST-shaped (body holds an array of CURIEs to avoid
    URL repetition for batches up to 200 terms) but is functionally a
    read-only lookup with no state mutation. Anonymous visitors hit it
    on every dataset page render, before they've had a chance to call
    /api/auth/csrf. Pre-fix, every anonymous summary-table view
    surfaced a "1 warning · ontology lookup failed" banner because the
    POST 403'd. Adding the path to EXEMPT_PATHS lets the middleware
    pass anonymous requests through to the router. (Visual-UX audit
    a395 P0 #3, 2026-05-14.)
    """
    assert "/api/ontology/batch-lookup" in EXEMPT_PATHS
