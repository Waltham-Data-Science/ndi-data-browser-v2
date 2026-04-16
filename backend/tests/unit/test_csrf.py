from backend.middleware.csrf import generate_token, sign, verify


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
