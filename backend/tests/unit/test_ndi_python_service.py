"""Unit tests for the NDI-python service wrappers.

These tests don't require the NDI-python stack to be installed (CI may
not have it). The service is designed to degrade gracefully when the
imports fail — and that's exactly what these tests pin down. When the
stack IS available, additional integration tests in the experimental
Railway env will exercise the real decoder paths against the production
Haley / Dabrowska binaries.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services import ndi_python_service


@pytest.fixture(autouse=True)
def reset_available_cache():
    """Ensure each test starts with a fresh NDI-availability probe.

    The service caches the result of its first import attempt to avoid
    re-paying the cost; tests need to clear that cache so they can
    independently force-on or force-off the stack.
    """
    ndi_python_service._NDI_AVAILABLE = None
    yield
    ndi_python_service._NDI_AVAILABLE = None


# ---------------------------------------------------------------------------
# is_ndi_compressed — pure byte-prefix check, no NDI dependency
# ---------------------------------------------------------------------------


class TestIsNdiCompressed:
    def test_detects_gzip_magic(self):
        assert ndi_python_service.is_ndi_compressed(b"\x1f\x8b\x08\x00") is True

    def test_rejects_short_payload(self):
        assert ndi_python_service.is_ndi_compressed(b"") is False
        assert ndi_python_service.is_ndi_compressed(b"\x1f") is False

    def test_rejects_non_gzip_payloads(self):
        assert ndi_python_service.is_ndi_compressed(b"VHSB") is False
        assert ndi_python_service.is_ndi_compressed(b"This is a VHSB file") is False
        assert ndi_python_service.is_ndi_compressed(b"\x00\x00\x00\x00") is False

    def test_only_inspects_first_two_bytes(self):
        # Gzip-magic prefix followed by garbage. Detection passes; the
        # downstream expand call would surface the real format issue.
        assert ndi_python_service.is_ndi_compressed(b"\x1f\x8b" + b"junk" * 100) is True


# ---------------------------------------------------------------------------
# read_vhsb_from_bytes — graceful degradation when NDI unavailable
# ---------------------------------------------------------------------------


class TestReadVhsbFromBytes:
    def test_returns_none_when_ndi_unavailable(self):
        ndi_python_service._NDI_AVAILABLE = False
        result = ndi_python_service.read_vhsb_from_bytes(b"This is a VHSB file" + b"\x00" * 2100)
        assert result is None

    def test_returns_none_on_short_payload(self):
        ndi_python_service._NDI_AVAILABLE = True
        # Minimum VHSB payload is 200 (text tag) + 1836 (header) = 2036 bytes
        result = ndi_python_service.read_vhsb_from_bytes(b"This is a VHSB file")
        assert result is None

    def test_returns_none_on_empty_payload(self):
        ndi_python_service._NDI_AVAILABLE = True
        assert ndi_python_service.read_vhsb_from_bytes(b"") is None

    def test_returns_none_when_vhsb_read_raises(self):
        """When the real vlt call raises (malformed payload, etc.), we
        swallow and return None so callers can fall through to their
        legacy soft-error path. No exception escapes the service."""
        ndi_python_service._NDI_AVAILABLE = True
        with patch.dict(
            "sys.modules",
            {"vlt.file.custom_file_formats": None},
        ):
            # Module-set-to-None forces ImportError on `from vlt.file...`
            result = ndi_python_service.read_vhsb_from_bytes(b"x" * 3000)
            assert result is None


# ---------------------------------------------------------------------------
# expand_ephys_from_bytes — graceful degradation
# ---------------------------------------------------------------------------


class TestExpandEphysFromBytes:
    def test_returns_none_when_ndi_unavailable(self):
        ndi_python_service._NDI_AVAILABLE = False
        result = ndi_python_service.expand_ephys_from_bytes(b"\x1f\x8b" + b"x" * 100)
        assert result is None

    def test_returns_none_on_non_compressed_payload(self):
        # Caller is supposed to gate on is_ndi_compressed first, but the
        # wrapper double-checks defensively.
        ndi_python_service._NDI_AVAILABLE = True
        result = ndi_python_service.expand_ephys_from_bytes(b"VHSB" + b"x" * 100)
        assert result is None

    def test_returns_none_when_ndicompress_fails(self):
        ndi_python_service._NDI_AVAILABLE = True
        with patch.dict("sys.modules", {"ndicompress": None}):
            result = ndi_python_service.expand_ephys_from_bytes(b"\x1f\x8b" + b"x" * 100)
            assert result is None


# ---------------------------------------------------------------------------
# lookup_ontology — never raises, returns None on miss
# ---------------------------------------------------------------------------


class TestLookupOntology:
    def test_returns_none_on_malformed_curie(self):
        # No `:` separator → not a CURIE → don't even probe.
        assert ndi_python_service.lookup_ontology("WBStrain00000001") is None

    def test_returns_none_on_empty_input(self):
        assert ndi_python_service.lookup_ontology("") is None

    def test_returns_none_when_ndi_unavailable(self):
        ndi_python_service._NDI_AVAILABLE = False
        result = ndi_python_service.lookup_ontology("CL:0000540")
        assert result is None

    def test_returns_none_on_ndi_miss(self):
        """NDI's lookup is documented to never raise — it returns a
        falsy OntologyResult on miss. Make sure we surface None upward,
        not an empty dict."""

        class _FakeResult:
            id = ""
            name = ""

            def __bool__(self):
                return False

            def to_dict(self):
                return {}

        ndi_python_service._NDI_AVAILABLE = True
        # ndi isn't installed in the test env, so we inject a fake module
        # via sys.modules. The wrapper imports lazily via `from ndi.ontology
        # import lookup` so monkey-patching sys.modules is the cleanest way.
        fake_module = type("M", (), {"lookup": lambda _curie: _FakeResult()})
        with patch.dict("sys.modules", {"ndi.ontology": fake_module}):
            result = ndi_python_service.lookup_ontology("CL:0000540")
            assert result is None

    def test_returns_dict_on_ndi_hit(self):
        class _FakeResult:
            id = "0000540"
            name = "T cell"
            prefix = "CL"
            definition = "Mature T cell."
            synonyms = []
            short_name = "T cell"

            def __bool__(self):
                return True

            def to_dict(self):
                return {
                    "id": self.id,
                    "name": self.name,
                    "prefix": self.prefix,
                    "definition": self.definition,
                    "synonyms": self.synonyms,
                    "short_name": self.short_name,
                }

        ndi_python_service._NDI_AVAILABLE = True
        fake_module = type("M", (), {"lookup": lambda _curie: _FakeResult()})
        with patch.dict("sys.modules", {"ndi.ontology": fake_module}):
            result = ndi_python_service.lookup_ontology("CL:0000540")
            assert result is not None
            assert result["name"] == "T cell"
            assert result["prefix"] == "CL"

    def test_swallows_ndi_exception(self):
        """Defensive: even though NDI is documented not to raise, if it
        does, we swallow + return None so callers don't see exceptions."""
        ndi_python_service._NDI_AVAILABLE = True

        def _boom(_curie):
            raise RuntimeError("boom")

        fake_module = type("M", (), {"lookup": _boom})
        with patch.dict("sys.modules", {"ndi.ontology": fake_module}):
            result = ndi_python_service.lookup_ontology("CL:0000540")
            assert result is None


# ---------------------------------------------------------------------------
# is_ndi_available — caches result, doesn't crash on missing imports
# ---------------------------------------------------------------------------


class TestIsNdiAvailable:
    def test_caches_first_result(self):
        ndi_python_service._NDI_AVAILABLE = True
        # Without resetting, subsequent calls should not re-import.
        assert ndi_python_service.is_ndi_available() is True
        ndi_python_service._NDI_AVAILABLE = False
        assert ndi_python_service.is_ndi_available() is False

    def test_returns_false_when_imports_fail(self):
        ndi_python_service._NDI_AVAILABLE = None
        with patch.dict(
            "sys.modules",
            {
                "vlt.file.custom_file_formats": None,
                "ndicompress": None,
                "ndi.ontology": None,
            },
        ):
            assert ndi_python_service.is_ndi_available() is False
            # And the cache survives:
            assert ndi_python_service._NDI_AVAILABLE is False
