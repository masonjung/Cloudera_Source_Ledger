"""The Harden gate for `scripts/doctor.py` — the pre-demo preflight.

A diagnostic that crashes when the network is down is worse than no diagnostic at
all: a broken network is precisely when it gets run. These tests sever the network
and assert it still reports, cleanly, in full.

No test here touches a real service. The one that would is `make doctor` itself.
"""

import importlib.util
import ssl
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def doctor():
    """Import `scripts/doctor.py`, which is a script rather than a package member."""
    spec = importlib.util.spec_from_file_location(
        "source_ledger_doctor", ROOT / "scripts" / "doctor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def offline(doctor, monkeypatch):
    """Sever every probe, the way an unreachable network would."""
    def boom(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(doctor.source_ledger, "text_to_urls", boom)


def test_reports_cleanly_with_no_network(doctor, offline, capsys):
    """The failure mode that matters: every probe down, and a verdict anyway."""
    exit_code = doctor.main()
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "VERDICT" in out
    assert "Nothing is reachable" in out
    # Every provider and engine still named, so the report is a full picture rather
    # than a first failure.
    for label in ("wikipedia", "openalex", "arxiv", "duckduckgo", "yandex"):
        assert label in out


def test_a_healthy_run_exits_zero(doctor, monkeypatch, capsys):
    monkeypatch.setattr(doctor.source_ledger, "text_to_urls",
                        lambda text, **kw: ["https://example.com"])

    assert doctor.main() == 0
    assert "Demo-ready" in capsys.readouterr().out


def test_blocked_web_still_exits_zero_and_recommends_the_apis(doctor, monkeypatch,
                                                              capsys):
    """A blocked engine is a measurement of this network, not a defect — the framing
    `make test-live` already uses. It must not fail the target."""
    def selective(text, *, provider="ddgs", **kw):
        if provider == "ddgs":
            raise OSError("blocked")
        return ["https://example.com"]

    monkeypatch.setattr(doctor.source_ledger, "text_to_urls", selective)

    assert doctor.main() == 0
    out = capsys.readouterr().out
    assert "Web search is blocked" in out
    assert "wikipedia" in out


def test_a_fast_empty_answer_is_called_a_block(doctor, monkeypatch):
    """The heuristic the whole preflight turns on: a refusal returns instantly, a
    real search does not. ddgs cannot tell them apart, so timing is all that is left."""
    monkeypatch.setattr(doctor.source_ledger, "text_to_urls", lambda text, **kw: [])
    label, status, detail, _ = doctor._probe("web: yahoo",
                                             lambda: doctor.source_ledger.text_to_urls("x"))

    assert status == doctor.FAIL
    assert "probably blocked" in detail


def test_a_slow_empty_answer_is_only_a_warning(doctor, monkeypatch):
    """Reachable but empty is not the same claim as blocked, and must not be made."""
    monkeypatch.setattr(doctor, "BLOCK_SECONDS", 0)
    label, status, detail, _ = doctor._probe("web: yahoo", lambda: [])

    assert status == doctor.WARN
    assert "reachable" in detail


def test_tls_interception_is_named_not_worked_around(doctor):
    """A repo with a governance layer does not answer TLS interception by disabling
    verification. It says what happened and stops."""
    detail = doctor._describe(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))

    assert "intercepting TLS" in detail
    assert "not bypassed" in detail


def test_the_report_is_ascii(doctor, offline, capsys):
    """The Windows console this is demoed from is not UTF-8, and a preflight that
    prints mojibake reads as a broken tool at exactly the wrong moment."""
    doctor.main()

    capsys.readouterr().out.encode("ascii")
