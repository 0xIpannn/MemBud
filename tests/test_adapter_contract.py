"""The adapter contract, written once and run on every guarded adapter.

Each test states one clause of ``data/adapter-contract.md`` section 5.  A harness supplies the
backend-specific driving code (``adapter_harness.py``); nothing below knows how a backend stores
its data.  Mem0-based harnesses need the pinned env (``requirements-mem0.txt``) and skip otherwise.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
os.environ["MEM0_TELEMETRY"] = "False"
os.environ.setdefault("MEM0_DIR", tempfile.mkdtemp(prefix="membud-mem0-dir-"))

from adapter_harness import HARNESSES, OTHER, SCOPE, SEED, Out  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(params=sorted(HARNESSES))
def h(request, tmp_path):
    if request.param.startswith("mem0"):
        pytest.importorskip("mem0")
        pytest.importorskip("spacy")
    harness = HARNESSES[request.param](tmp_path)
    yield harness
    harness.close()


def seeded(h):
    out = h.add(list(SEED))
    assert out.decision == "ACCEPT" and out.applied, out
    return h


# -- non-vacuity ------------------------------------------------------------------------------


def test_control_unguarded_write_changes_every_surface(h):
    """If an unguarded write did not change the surfaces, byte-identity below would prove nothing."""
    assert h.control_changes_surfaces()


# -- HOLD / REJECTED leave every observable write surface byte-identical ---------------------


def test_hold_unsupported_add_is_byte_identical(h):
    seeded(h)
    before = h.surfaces()
    out = h.add(["Alice lives on Mars"], supported_by=["Alice likes tea"])
    assert out.decision == "HOLD" and not out.applied, out
    assert h.surfaces() == before


def test_hold_unsupported_update_is_byte_identical(h):
    seeded(h)
    before = h.surfaces()
    out = h.update(SEED[0], "Alice lives on Mars", supported=False)
    assert out.decision == "HOLD" and not out.applied, out
    assert h.surfaces() == before


def test_unauthorized_delete_is_rejected_and_byte_identical(h):
    seeded(h)
    before = h.surfaces()
    out = h.delete(SEED[0], authorized=False)
    assert out.decision == "REJECTED" and not out.applied, out
    assert h.surfaces() == before


# -- ACCEPT persists exactly the verified state ----------------------------------------------


def test_accept_add_persists_exactly_the_verified_state(h):
    before = h.surfaces()
    out = h.add(list(SEED))
    assert out.decision == "ACCEPT" and out.applied, out
    assert sorted(h.texts()) == sorted(SEED)
    assert h.surfaces() != before


def test_accept_update_and_delete_persist_exactly(h):
    seeded(h)
    upd = h.update(SEED[0], "Alice lives in Paris", supported=True)
    assert upd.decision == "ACCEPT" and upd.applied, upd
    assert sorted(h.texts()) == sorted(["Alice lives in Paris", SEED[1]])
    dele = h.delete(SEED[1], authorized=True)
    assert dele.decision == "ACCEPT" and dele.applied, dele
    assert h.texts() == ["Alice lives in Paris"]


def test_other_scopes_are_untouched_by_an_accept(h):
    h.unguarded_add("Bob owns a boat", OTHER)
    before = h.texts(OTHER)
    seeded(h)
    assert h.texts(OTHER) == before == ["Bob owns a boat"]


# -- stale state is refused -------------------------------------------------------------------


def test_stale_state_is_refused_and_the_intruder_survives(h):
    with h.race():
        out = h.add(["Alice lives in Berlin"])
    assert out.decision == "REJECTED" and not out.applied, out
    assert h.texts() == ["Intruder wrote this"]


# -- injected failure is compensated ----------------------------------------------------------


def test_commit_failure_is_compensated_to_the_prior_public_state(h):
    seeded(h)
    before = h.texts()
    with h.fail_write(permanent=False):
        out = h.add(["Alice has a dog named Rex", "Alice owns a red bicycle"])
    assert out.decision == "REJECTED" and not out.applied, out
    assert h.texts() == before


def test_unrecoverable_failure_is_loud_not_silent(h):
    seeded(h)
    with h.fail_write(permanent=True):
        out = h.add(["Alice has a dog named Rex", "Alice owns a red bicycle"])
    assert out.decision == "REJECTED" and not out.applied, out
    assert "could not" in out.reason.lower()


# -- errors fail closed -----------------------------------------------------------------------


def test_backend_error_while_staging_writes_nothing(h):
    seeded(h)
    before = h.surfaces()
    with h.native_error():
        out = h.add(["Alice has a dog named Rex"])
    assert not out.applied and out.decision in {"ERROR", "REJECTED"}, out
    assert h.surfaces() == before


def test_direct_writes_through_the_guard_are_refused(h):
    seeded(h)
    before = h.surfaces()
    with pytest.raises(Exception, match="no staged"):
        h.direct_replace()
    assert h.surfaces() == before


@pytest.mark.parametrize("kind", ["foreign_scope", "history_rewrite"])
def test_unmodeled_side_effect_is_rejected_and_byte_identical(h, kind):
    if kind == "history_rewrite" and not getattr(h, "has_history", True):
        pytest.skip("backend exposes no history surface: a history rewrite is not expressible (documented blind spot)")
    seeded(h)
    before = h.surfaces()
    with h.tamper(kind):
        out = h.add(["Alice lives in Berlin and Alice works at Acme Corp"])
    assert out.decision == "REJECTED" and not out.applied, out
    assert out.error_type in {"UnmodeledChange", "UnmodeledSideEffect"}, out
    assert h.surfaces() == before


# -- capability matrix: effects an adapter may or may not see (documented, asserted) ---------


@pytest.mark.parametrize("kind", ["schema_change", "hidden_surface"])
def test_blind_spots_are_exactly_the_documented_ones(h, kind):
    seeded(h)
    with h.tamper(kind):
        out = h.add(["Alice lives in Berlin and Alice works at Acme Corp"])
    if h.detects[kind]:
        assert out.decision == "REJECTED" and not out.applied, out
    else:
        # invisible through the public API: neither detected nor replayed (see docs: limits)
        assert out.decision != "REJECTED" or "unmodeled" not in out.reason, out
