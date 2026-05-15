"""Static regression test for the audit-log-policy promise.

The public `/security` page promises:

  > Every API call is logged with user, timestamp, action, and outcome.
  > Request bodies and response payloads are explicitly excluded — so
  > PHI cannot leak into logs by accident.

This test enforces that promise structurally: every `log.X(...)` call in
the backend is parsed with `ast`, and the keyword-argument names are
checked against a denylist of PHI / secret-shaped names. A new log line
introducing `password=`, `email=` (unhashed), `request_body=`, etc. fails
the build before it ships.

The denylist is conservative — it catches both "obvious leak" names and
"surface that could carry PHI" names like `body` / `payload`. Adding a
new logger that legitimately needs one of these names (e.g. ALLOWLISTED
debug logging of a structured response shape that has been audited)
should be done by adding an explicit `# noqa: phi-in-logs` marker on the
log line + an entry in `ALLOWED_LINE_MARKERS` below, with a brief comment
explaining why the audit is OK.

Doc reference: `apps/web/docs/operations/hipaa-technical-safeguards.md`
§164.312(b) Audit controls, Verification test row.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Names that suggest a log line carries plaintext PHI or a secret.
# Hashes and IDs are fine: `user_id_hash`, `session_id`, `request_id`,
# `email_hash` all pass. The names below are the bare versions.
PHI_DENYLIST: frozenset[str] = frozenset({
    # Authentication secrets
    "password",
    "passwd",
    "pwd",
    "secret",
    "raw_password",
    "plain_password",
    "access_token",
    "refresh_token",
    "bearer_token",
    "csrf_raw",
    "csrf_cookie",
    # Request / response surface (these are the PHI-bearing fields)
    "body",
    "request_body",
    "req_body",
    "response_body",
    "resp_body",
    "payload",
    "request_payload",
    "response_payload",
    # PII surface — must be hashed before logging
    "email",
    "email_raw",
    "phone",
    "phone_number",
    "ssn",
    "dob",
    "date_of_birth",
    "raw_user_agent",
    "raw_ip",
    "ip_address",
    "user_agent",  # use `user_agent_hash` instead
})

# Names that have hashes / truncations and are SAFE despite looking
# similar to denylisted names. Tracked here to make the safe-vs-unsafe
# boundary explicit rather than implicit in the denylist.
SAFE_NAME_PATTERNS: tuple[str, ...] = (
    "_hash",
    "_hashed",
    "_digest",
    "_short",
    "_truncated",
)

# Lines that have been audited and exempted, as ``<rel-path>:<line>``
# strings (e.g. ``auth/login.py:105``). Empty by design — every entry
# represents a documented exception. Add only with an accompanying
# audit note explaining why the log call is safe despite using one
# of the PHI_DENYLIST names.
ALLOWED_LINE_MARKERS: frozenset[str] = frozenset()


def _backend_root() -> Path:
    """Resolve the `backend/` package root from this test file."""
    return Path(__file__).resolve().parents[2]


def _python_source_files(root: Path) -> list[Path]:
    """Walk the `backend/` tree for .py files, skipping tests + caches."""
    paths: list[Path] = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("tests/") or rel.startswith("__pycache__/"):
            continue
        if "/__pycache__/" in rel:
            continue
        paths.append(p)
    return paths


def _is_logger_call(node: ast.Call) -> bool:
    """Match `log.X(...)`, `logger.X(...)`, `LOG.X(...)`, etc.

    Heuristic: attribute access where the method name is one of the
    structlog levels and the receiver's lowercased name contains `log`.
    Tolerates dotted receivers like `self.log.info(...)`.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "msg",
    }:
        return False
    # Walk the receiver chain looking for a part whose lowercased name
    # contains `log`. Skip the first attr (the level name).
    receiver: ast.AST = func.value
    while isinstance(receiver, ast.Attribute):
        if "log" in receiver.attr.lower():
            return True
        receiver = receiver.value
    return isinstance(receiver, ast.Name) and "log" in receiver.id.lower()


def _safe_by_pattern(kw_name: str) -> bool:
    """The `_hash`/`_truncated`/etc.-suffixed names are safe by convention."""
    return any(kw_name.endswith(p) for p in SAFE_NAME_PATTERNS)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return [(line_no, denylisted_kwarg_name), ...] for the given file.

    Empty list means no findings.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_logger_call(node):
            continue
        for kw in node.keywords:
            if kw.arg is None:
                continue
            name = kw.arg
            if _safe_by_pattern(name):
                continue
            if name in PHI_DENYLIST:
                findings.append((node.lineno, name))
    return findings


@pytest.mark.parametrize("source_path", _python_source_files(_backend_root()))
def test_no_phi_in_log_calls(source_path: Path) -> None:
    """Every log.X() call must avoid PHI / secret-shaped kwarg names.

    Failure means a new log line was introduced with a kwarg name from
    the denylist. Either rename to a hashed/truncated form, OR add an
    explicit `# noqa: phi-in-logs` comment in the source + an entry in
    ALLOWED_LINE_MARKERS above with a brief audit justification.
    """
    findings = _scan_file(source_path)
    backend_root = _backend_root()
    rel = source_path.relative_to(backend_root).as_posix()
    findings = [
        f for f in findings if f"{rel}:{f[0]}" not in ALLOWED_LINE_MARKERS
    ]
    if findings:
        details = "\n".join(
            f"  {rel}:{lineno} — kwarg `{name}` is in PHI_DENYLIST"
            for lineno, name in findings
        )
        pytest.fail(
            f"PHI / secret-shaped kwargs found in log calls:\n{details}\n\n"
            "Either:\n"
            "  (a) Rename the kwarg to the hashed / truncated form (e.g.\n"
            "      `email_hash` instead of `email`, `session_id[:8]` value\n"
            "      under the existing `session_id` key).\n"
            "  (b) If the value is genuinely safe to log (e.g. an audited\n"
            "      enum), add `# noqa: phi-in-logs` on the source line AND\n"
            "      an entry in `ALLOWED_LINE_MARKERS` in this test file with\n"
            "      a brief explanation."
        )


def test_phi_denylist_is_non_empty() -> None:
    """Belt-and-suspenders: the denylist itself isn't empty.

    A future refactor that accidentally clears the set would silently
    pass the parametrized test (zero findings on every file). This
    sanity check catches that.
    """
    assert PHI_DENYLIST, "PHI_DENYLIST must contain entries"
    assert "password" in PHI_DENYLIST
    assert "body" in PHI_DENYLIST
    assert "email" in PHI_DENYLIST
