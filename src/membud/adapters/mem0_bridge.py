"""Host-owned staged-proposal bridge for a Mem0-style extractor (Phase 0).

**This is not a hook into unmodified Mem0.**  Source inspection of the pinned
PyPI release ``mem0ai==2.2.1`` (wheel ``mem0ai-2.2.1-py3-none-any.whl``,
SHA-256 ``fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d``)
found no public callback, transaction, or pre-commit hook that exposes the
complete proposed transition before Mem0 writes it to its vector store, history,
messages, and entity store; see ``data/backend-feasibility.md``.  This module
therefore never imports, wraps, monkeypatches, or calls Mem0.

The bridge models a *host-owned* pipeline instead:

1. the host calls its Mem0-style extractor and normalizes the result to the
   plain-dict proposal contract documented below;
2. :meth:`Mem0ProposalBridge.prepare_proposals` deterministically maps that
   proposal set plus a prior canonical snapshot into a schema-version-2
   :class:`membud.schemas.Transition` and an explicit :class:`StagedProposal`;
3. :func:`apply_staged` (or ``StagedProposal.apply``) composes the reference
   :class:`membud.adapters.reference.GuardedMemory` verification path: the
   transition is verified and the staged candidate is persisted exactly once,
   and only on ``ACCEPT``;
4. ``HOLD``/invalid input never persist anything, and preparation performs no
   writes at all (it is pure mapping plus transition validation).

Provenance note
---------------
The release pin and wheel digest above come from ``data/backend-feasibility.md``
(source inspection only; the wheel was downloaded and unpacked, never installed
or executed).  The proposal shape below is a host-normalized *contract* modeled
on Mem0's public memory record/metadata/scoping shapes and its inferred event
vocabulary (``ADD``/``UPDATE``/``DELETE``/``NONE``).  It is **unverified against a
running Mem0** and must not be read as a compatibility statement: the exact
extracted payload of ``_add_to_vector_store`` was not runtime-tested, and no
write side effect of Mem0 is guarded by this module.  See
``data/mem0-bridge.md`` for the threat model and the write-side-effect checklist
that must be proven before any compatibility claim.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..schemas import SCHEMA_VERSION, Evidence, Record, Transition
from ..serialization import transition_dict
from .base import AdapterContractError, PriorState, canonical_records
from .reference import GuardedDecision, GuardedMemory

__all__ = [
    "MEM0_RELEASE",
    "MEM0_SHAPE_CONTRACT",
    "MEM0_WHEEL",
    "MEM0_WHEEL_SHA256",
    "Mem0BridgeError",
    "Mem0ProposalBridge",
    "StagedProposal",
    "apply_staged",
    "mem0_precommit_contract",
]

#: Pinned Mem0 release the modelled/proposal shape is documented against.
MEM0_RELEASE = "mem0ai==2.2.1"
#: Wheel filename recorded in ``data/backend-feasibility.md``.
MEM0_WHEEL = "mem0ai-2.2.1-py3-none-any.whl"
#: SHA-256 of that wheel, from ``data/backend-feasibility.md``.
MEM0_WHEEL_SHA256 = (
    "fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d"
)

#: Proposal keys accepted by :meth:`Mem0ProposalBridge.prepare_proposals`.
#: Unknown keys are rejected so a host cannot smuggle an undeclared mutation in.
MEM0_PROPOSAL_KEYS = (
    "event",
    "id",
    "memory",
    "metadata",
    "user_id",
    "agent_id",
    "run_id",
)

#: Mem0 inference event -> canonical operation mapping used by the bridge.
MEM0_EVENT_OPERATIONS = {
    "ADD": "WRITE",
    "UPDATE": "REVISE",
    "DELETE": "DELETE",
    "NONE": "NONE (no change; a NOOP transition is produced if nothing else changes)",
}

#: Machine-readable description of the (unverified) shape contract.
MEM0_SHAPE_CONTRACT = {
    "backend": "mem0",
    "release": MEM0_RELEASE,
    "wheel": MEM0_WHEEL,
    "wheel_sha256": MEM0_WHEEL_SHA256,
    "contract_status": "unverified_source_inspection_contract",
    "contract_document": "data/backend-feasibility.md",
    "proposal_keys": MEM0_PROPOSAL_KEYS,
    "event_to_operation": MEM0_EVENT_OPERATIONS,
    "runtime_tested_against_mem0": False,
    "imports_mem0": False,
    "guard_is_a_hook_into_unmodified_mem0": False,
    "notes": (
        "Plain-dict proposal shape normalized by the host; modeled on Mem0's "
        "public record/metadata/scope shapes and inferred event vocabulary. "
        "The mem0ai==2.2.1 wheel sha256 pins the source-inspection provenance "
        "only; it is not evidence of compatibility."
    ),
}

#: Normative statement of the host-owned staging requirement.  Kept as a
#: constant so tests and documentation can quote it verbatim.
mem0_precommit_contract = """\
HOST-OWNED STAGED-PROPOSAL CONTRACT -- THIS IS NOT A HOOK INTO UNMODIFIED MEM0.

The pinned source inspection of mem0ai==2.2.1 (wheel sha256
fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d) found no
public callback, transaction, or pre-commit hook that exposes a complete
proposed transition before Mem0 writes its vector store, history, messages, and
entity store (data/backend-feasibility.md).  Wrapping Memory.add() after the
fact is too late; intercepting private persistence internals is unstable and
partial.

Therefore the host must own extraction and staging:
  1. extract proposals itself (or normalize a host-side extractor result) into
     plain dicts following the documented proposal contract;
  2. hand them, with an explicit prior canonical snapshot, to
     Mem0ProposalBridge.prepare_proposals(), which ONLY maps and validates them
     into a schema-v2 Transition ("staged candidate").  It must not write;
  3. persist exclusively through apply_staged()/StagedProposal.apply(), which
     composes the reference GuardedMemory: verification runs first and the
     candidate is applied exactly once, only on ACCEPT;
  4. on HOLD, invalid input, stale prior state, or any verification/apply error
     the backend must be left untouched.

This module does not intercept, wrap, monkeypatch, import, or call Mem0.  No
Mem0 compatibility is claimed, and no Mem0 write side effect is guarded.  See
data/mem0-bridge.md for the checklist required before any such claim.
"""

_EVENTS = frozenset({"ADD", "UPDATE", "DELETE", "NONE"})
_SCOPE_KEYS = ("user_id", "agent_id", "run_id")
_AUTHORIZATION_KEYS = frozenset({"source", "evidence_id", "rationale", "target_id"})


class Mem0BridgeError(AdapterContractError):
    """Raised when a Mem0-style proposal violates the staging contract.

    Preparation is fail-closed: any missing/unknown field, ambiguous or
    duplicate target, non-canonical value, or invalid transition raises this
    error *before* any verification, audit-store write, or backend write.
    """


# ---------------------------------------------------------------------------
# Staging object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StagedProposal:
    """An explicit, inspectable-but-not-writable staging result.

    ``transition`` is the complete canonical schema-v2 transition (with
    ``before``, ``candidate``, evidence/provenance, required claims, and
    declared operations).  ``candidate`` is the complete proposed scoped state.
    Creating a staged proposal performs no persistence; a host must not write
    it directly.  The only sanctioned persistence path is
    :func:`apply_staged` / :meth:`apply`, which writes exactly the candidate and
    only after ``GuardedMemory`` verification returned ``ACCEPT``.
    """

    scope: tuple[tuple[str, str], ...]
    case_id: str
    prior_records: tuple[Record, ...]
    transition: Transition
    provenance: dict[str, Any]
    digest: str
    release: str = MEM0_RELEASE

    @property
    def candidate(self) -> tuple[Record, ...]:
        """Complete proposed scoped state (staged; not yet persisted)."""
        return self.transition.candidate

    @property
    def operations(self) -> tuple[Any, ...]:
        return self.transition.operations

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return self.transition.evidence

    def scope_mapping(self) -> dict[str, str]:
        return dict(self.scope)

    def transition_payload(self) -> dict[str, Any]:
        """Return the canonical schema-v2 transition as a plain dict."""
        return transition_dict(self.transition)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "event": "mem0_staged_proposal",
            "release": self.release,
            "case_id": self.case_id,
            "scope": self.scope_mapping(),
            "digest": self.digest,
            "prior_records": [asdict(record) for record in self.prior_records],
            "transition": self.transition_payload(),
            "provenance": self.provenance,
        }

    def apply(self, guard: GuardedMemory, *, run_id: str | None = None) -> GuardedDecision:
        """Verify and persist through ``GuardedMemory`` (ACCEPT-only)."""
        return apply_staged(guard, self, run_id=run_id)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _canonical_scope(scope: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(scope, Mapping):
        raise Mem0BridgeError("scope must be a mapping of string identifiers")
    pairs: list[tuple[str, str]] = []
    for key, value in scope.items():
        if not isinstance(key, str) or not key.strip():
            raise Mem0BridgeError("scope keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise Mem0BridgeError(f"scope value for {key!r} must be a non-empty string")
        pairs.append((key, value))
    if not pairs:
        raise Mem0BridgeError(
            "scope must declare at least one identifier (e.g. user_id); "
            "unscoped memory writes are rejected"
        )
    return tuple(sorted(pairs))


def _canonical_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Mem0BridgeError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise Mem0BridgeError(
            f"{field_name} must not have leading/trailing whitespace (non-canonical value)"
        )
    return value


def _derived_record_id(scope: tuple[tuple[str, str], ...], memory: str) -> str:
    seed = json.dumps(
        {"scope": dict(scope), "memory": memory},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "mem0-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _normalize_evidence(items: Any) -> tuple[Evidence, ...]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Iterable):
        raise Mem0BridgeError("evidence must be an iterable of Evidence values or dicts")
    normalized: list[Evidence] = []
    for item in items:
        if isinstance(item, Evidence):
            normalized.append(item)
        elif isinstance(item, Mapping):
            try:
                normalized.append(Evidence.from_dict(dict(item)))
            except (KeyError, TypeError, ValueError) as exc:
                raise Mem0BridgeError(f"invalid evidence item: {exc}") from exc
        else:
            raise Mem0BridgeError(
                f"evidence must be Evidence values or dicts, got {type(item).__name__}"
            )
    return tuple(normalized)


def _normalize_prior(prior: Any, scope: tuple[tuple[str, str], ...]) -> tuple[Record, ...]:
    if isinstance(prior, PriorState):
        if tuple(sorted(prior.scope)) != scope:
            raise Mem0BridgeError("prior snapshot scope does not match the bridge scope")
        return tuple(prior.records)
    try:
        return canonical_records(prior)
    except (TypeError, ValueError) as exc:
        raise Mem0BridgeError(f"prior snapshot is not canonical: {exc}") from exc


def _normalize_required_claims(required_claims: Any) -> tuple[str, ...]:
    if required_claims is None:
        return ()
    if isinstance(required_claims, (str, bytes)) or not isinstance(required_claims, Iterable):
        raise Mem0BridgeError("required_claims must be an iterable of non-empty strings")
    claims: list[str] = []
    for claim in required_claims:
        claims.append(_canonical_text(claim, "required claim"))
    return tuple(claims)


def _normalize_metadata(raw: Any, index: int) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise Mem0BridgeError(f"proposal[{index}].metadata must be a mapping or null")
    metadata: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise Mem0BridgeError(f"proposal[{index}].metadata keys must be strings")
        metadata[key] = value
    try:
        json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise Mem0BridgeError(
            f"proposal[{index}].metadata must be JSON-serializable: {exc}"
        ) from exc
    return metadata


def _proposal_scope(
    proposal: Mapping[str, Any],
    scope: tuple[tuple[str, str], ...],
    index: int,
) -> dict[str, str]:
    lookup = dict(scope)
    declared: dict[str, str] = {}
    for key in _SCOPE_KEYS:
        value = proposal.get(key)
        if value is None:
            continue
        value = _canonical_text(value, f"proposal[{index}].{key}")
        if key not in lookup:
            raise Mem0BridgeError(
                f"proposal[{index}] declares {key!r} but the bridge scope does not"
            )
        if lookup[key] != value:
            raise Mem0BridgeError(
                f"proposal[{index}] {key} {value!r} does not match the bridge scope"
            )
        declared[key] = value
    return declared


def _normalize_delete_authorization(
    raw: Any, record_id: str
) -> dict[str, str]:
    """Shape one structured DELETE authorization for ``Transition`` validation.

    Authorization is supplied by the host, never by the extractor's metadata:
    an LLM-extracted ``DELETE`` event is not by itself an authorization to
    forget data.
    """
    if raw is None:
        raise Mem0BridgeError(
            f"DELETE of {record_id!r} requires structured authorization metadata"
        )
    if not isinstance(raw, Mapping):
        raise Mem0BridgeError(f"DELETE authorization for {record_id!r} must be a mapping")
    unknown = set(raw) - _AUTHORIZATION_KEYS
    if unknown:
        raise Mem0BridgeError(
            f"DELETE authorization for {record_id!r} has unknown keys: "
            + ", ".join(sorted(map(str, unknown)))
        )
    target_id = raw.get("target_id")
    if target_id is not None and target_id != record_id:
        raise Mem0BridgeError(
            f"DELETE authorization target {target_id!r} does not match {record_id!r}"
        )
    rationale = _canonical_text(
        raw.get("rationale"), f"DELETE authorization rationale for {record_id!r}"
    )
    return {
        "source": _canonical_text(raw.get("source"), f"DELETE authorization source for {record_id!r}"),
        "evidence_id": _canonical_text(
            raw.get("evidence_id"), f"DELETE authorization evidence_id for {record_id!r}"
        ),
        "target_id": record_id,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class Mem0ProposalBridge:
    """Deterministically map Mem0-style proposals to a staged schema-v2 transition.

    The bridge is a pure mapping/validation layer: it never touches a backend.
    Persistence happens only through :func:`apply_staged`, which composes the
    reference guarded write path.  See the module docstring and
    :data:`mem0_precommit_contract`.
    """

    def __init__(self, *, release: str = MEM0_RELEASE) -> None:
        if release != MEM0_RELEASE:
            raise Mem0BridgeError(
                f"only the pinned source-inspection release {MEM0_RELEASE!r} is modelled; "
                "pin and document a new release before mapping against it"
            )
        self.release = release

    def prepare_proposals(
        self,
        *,
        scope: Mapping[str, str],
        prior: PriorState | Sequence[Record | Mapping[str, Any]],
        proposals: Sequence[Mapping[str, Any]],
        case_id: str,
        required_claims: Sequence[str] = (),
        evidence: Sequence[Evidence | Mapping[str, Any]] = (),
        delete_authorizations: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> StagedProposal:
        """Map a host-normalized Mem0 proposal set into a staged transition.

        ``prior`` is the explicit canonical prior snapshot (normally
        ``GuardedMemory.capture_prior(scope)``).  ``proposals`` are plain dicts
        with the documented keys (see :data:`MEM0_SHAPE_CONTRACT`).  Evidence
        and DELETE authorizations are supplied by the trusted host and are
        passed through unchanged; they are never invented from model output.

        Raises :class:`Mem0BridgeError` for any contract violation before any
        write path is entered.  A returned :class:`StagedProposal` carries the
        complete schema-v2 transition and the staged candidate; persisting it is
        only allowed through :func:`apply_staged`.
        """
        bridge_scope = _canonical_scope(scope)
        prior_records = _normalize_prior(prior, bridge_scope)
        prior_by_id = {record.id: record for record in prior_records}
        normalized_case_id = _canonical_text(case_id, "case_id")
        normalized_required = _normalize_required_claims(required_claims)
        normalized_evidence = _normalize_evidence(evidence)
        evidence_ids = {item.id for item in normalized_evidence}
        normalized_authorizations = self._normalize_authorizations(
            delete_authorizations, prior_by_id
        )

        if isinstance(proposals, (str, bytes, Mapping)) or not isinstance(
            proposals, Iterable
        ):
            raise Mem0BridgeError("proposals must be an iterable of plain dicts")
        proposal_list = list(proposals)
        if not proposal_list:
            raise Mem0BridgeError(
                "no proposals were extracted; refusing to stage an empty candidate set"
            )

        updates: dict[str, tuple[str, ...]] = {}
        deletes: dict[str, dict[str, Any]] = {}
        additions: list[Record] = []
        added_ids: set[str] = set()
        target_ids: set[str] = set()
        used_authorizations: set[str] = set()
        proposal_provenance: list[dict[str, Any]] = []

        for index, raw in enumerate(proposal_list):
            if not isinstance(raw, Mapping):
                raise Mem0BridgeError(
                    f"proposal[{index}] must be a plain mapping, got {type(raw).__name__}"
                )
            unknown = set(raw) - set(MEM0_PROPOSAL_KEYS)
            if unknown:
                raise Mem0BridgeError(
                    f"proposal[{index}] has unknown keys: "
                    + ", ".join(sorted(map(str, unknown)))
                )
            event = _canonical_text(raw.get("event"), f"proposal[{index}].event").upper()
            if event not in _EVENTS:
                raise Mem0BridgeError(
                    f"proposal[{index}] has unsupported event {event!r}; "
                    f"expected one of {sorted(_EVENTS)}"
                )
            declared_scope = _proposal_scope(raw, bridge_scope, index)
            metadata = _normalize_metadata(raw.get("metadata"), index)
            memory_raw = raw.get("memory")
            memory: str | None = None
            if memory_raw is not None:
                memory = _canonical_text(memory_raw, f"proposal[{index}].memory")
            record_id = self._record_id(raw.get("id"), index, bridge_scope, memory)
            entry = {
                "index": index,
                "event": event,
                "id": record_id,
                "metadata": metadata,
                "scope": declared_scope,
            }
            proposal_provenance.append(entry)

            if event == "ADD":
                if memory is None:
                    raise Mem0BridgeError(f"proposal[{index}] ADD requires a memory text")
                assert record_id is not None
                if record_id in prior_by_id:
                    raise Mem0BridgeError(
                        f"proposal[{index}] ADD id {record_id!r} already exists in the "
                        "prior state; use UPDATE"
                    )
                if record_id in added_ids:
                    raise Mem0BridgeError(
                        f"proposal[{index}] duplicates the staged record id {record_id!r}"
                    )
                added_ids.add(record_id)
                additions.append(Record(record_id, (memory,)))
            elif event == "UPDATE":
                if record_id is None or record_id not in prior_by_id:
                    raise Mem0BridgeError(
                        f"proposal[{index}] UPDATE id {record_id!r} does not reference "
                        "a prior record"
                    )
                if memory is None:
                    raise Mem0BridgeError(f"proposal[{index}] UPDATE requires a memory text")
                if record_id in target_ids:
                    raise Mem0BridgeError(
                        f"proposal[{index}] targets {record_id!r} more than once"
                    )
                target_ids.add(record_id)
                updates[record_id] = (memory,)
            elif event == "DELETE":
                if record_id is None or record_id not in prior_by_id:
                    raise Mem0BridgeError(
                        f"proposal[{index}] DELETE id {record_id!r} does not reference "
                        "a prior record"
                    )
                if memory is not None and memory not in prior_by_id[record_id].claims:
                    raise Mem0BridgeError(
                        f"proposal[{index}] DELETE memory does not match the prior record "
                        f"{record_id!r}; the proposal is stale or inconsistent"
                    )
                if record_id in target_ids:
                    raise Mem0BridgeError(
                        f"proposal[{index}] targets {record_id!r} more than once"
                    )
                authorization = normalized_authorizations.get(record_id)
                if authorization is None:
                    raise Mem0BridgeError(
                        f"proposal[{index}] DELETE of {record_id!r} requires structured "
                        "authorization metadata supplied by the host"
                    )
                used_authorizations.add(record_id)
                target_ids.add(record_id)
                deletes[record_id] = authorization
            else:  # NONE: an explicit no-op; it never changes state.
                continue

        unused = sorted(set(normalized_authorizations) - used_authorizations)
        if unused:
            raise Mem0BridgeError(
                "delete authorizations supplied for records that are not deleted: "
                + ", ".join(unused)
            )

        candidate = self._build_candidate(prior_records, updates, deletes, additions)
        operations = self._build_operations(prior_records, updates, deletes, additions)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "case_id": normalized_case_id,
            "before": [
                {"id": record.id, "claims": list(record.claims)} for record in prior_records
            ],
            "candidate": [
                {"id": record.id, "claims": list(record.claims)} for record in candidate
            ],
            "evidence": [
                {
                    "id": item.id,
                    "supports": list(item.supports),
                    "source": item.source,
                    "text": item.text,
                    "contradicts": list(item.contradicts),
                }
                for item in normalized_evidence
            ],
            "required_claims": list(normalized_required),
            "operations": operations,
        }
        try:
            transition = Transition.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise Mem0BridgeError(
                f"proposal mapping does not form a valid schema-v2 transition: {exc}"
            ) from exc

        provenance = {
            "backend": "mem0",
            "release": self.release,
            "wheel": MEM0_WHEEL,
            "wheel_sha256": MEM0_WHEEL_SHA256,
            "contract_status": "unverified_source_inspection_contract",
            "contract_document": "data/backend-feasibility.md",
            "runtime_tested_against_mem0": False,
            "scope": dict(bridge_scope),
            "proposal_count": len(proposal_provenance),
            "proposals": tuple(proposal_provenance),
        }
        digest = hashlib.sha256(
            json.dumps(
                transition_dict(transition),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return StagedProposal(
            scope=bridge_scope,
            case_id=normalized_case_id,
            prior_records=prior_records,
            transition=transition,
            provenance=provenance,
            digest=digest,
            release=self.release,
        )

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _normalize_authorizations(
        delete_authorizations: Mapping[str, Mapping[str, Any]] | None,
        prior_by_id: Mapping[str, Record],
    ) -> dict[str, dict[str, str]]:
        if delete_authorizations is None:
            return {}
        if not isinstance(delete_authorizations, Mapping):
            raise Mem0BridgeError("delete_authorizations must be a mapping by record id")
        normalized: dict[str, dict[str, str]] = {}
        for record_id, raw in delete_authorizations.items():
            if not isinstance(record_id, str) or record_id not in prior_by_id:
                raise Mem0BridgeError(
                    f"delete authorization for unknown prior record {record_id!r}"
                )
            normalized[record_id] = _normalize_delete_authorization(raw, record_id)
        return normalized

    @staticmethod
    def _record_id(
        raw_id: Any,
        index: int,
        scope: tuple[tuple[str, str], ...],
        memory: str | None,
    ) -> str | None:
        if raw_id is None:
            if memory is None:
                return None
            return _derived_record_id(scope, memory)
        return _canonical_text(raw_id, f"proposal[{index}].id")

    @staticmethod
    def _build_candidate(
        prior_records: Sequence[Record],
        updates: Mapping[str, tuple[str, ...]],
        deletes: Mapping[str, Mapping[str, Any]],
        additions: Sequence[Record],
    ) -> tuple[Record, ...]:
        candidate: list[Record] = []
        for record in prior_records:
            if record.id in deletes:
                continue
            if record.id in updates:
                candidate.append(Record(record.id, updates[record.id]))
            else:
                candidate.append(record)
        candidate.extend(sorted(additions, key=lambda record: record.id))
        return tuple(candidate)

    @staticmethod
    def _build_operations(
        prior_records: Sequence[Record],
        updates: Mapping[str, tuple[str, ...]],
        deletes: Mapping[str, Mapping[str, Any]],
        additions: Sequence[Record],
    ) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        for record in prior_records:
            if record.id in updates:
                operations.append(
                    {"type": "REVISE", "record_id": record.id, "target_id": record.id}
                )
            elif record.id in deletes:
                authorization = deletes[record.id]
                operations.append(
                    {
                        "type": "DELETE",
                        "target_id": record.id,
                        "reason": authorization["rationale"],
                        "authorization": dict(authorization),
                    }
                )
        for record in sorted(additions, key=lambda item: item.id):
            operations.append({"type": "WRITE", "record_id": record.id})
        if not operations:
            operations.append({"type": "NOOP"})
        return operations


# ---------------------------------------------------------------------------
# Apply helper (composes the reference guarded write path; no modification)
# ---------------------------------------------------------------------------


def _operation_payload(operation: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": operation.type}
    if operation.record_id is not None:
        payload["record_id"] = operation.record_id
    if operation.target_id is not None:
        payload["target_id"] = operation.target_id
    if operation.reason:
        payload["reason"] = operation.reason
    if operation.authorization is not None:
        authorization = operation.authorization
        payload["authorization"] = {
            "source": authorization.source,
            "evidence_id": authorization.evidence_id,
            "target_id": authorization.target_id,
            "rationale": authorization.rationale,
        }
    return payload


def apply_staged(
    guard: GuardedMemory,
    staged: StagedProposal,
    *,
    run_id: str | None = None,
) -> GuardedDecision:
    """Verify a staged proposal and persist it only on ``ACCEPT``.

    This helper is deliberately thin: it passes the staged canonical
    transition's candidate, operations, evidence, and required claims back
    through :meth:`GuardedMemory.propose_update`.  Verification, prior-state
    re-checking, ACCEPT-only ``replace_state``, post-apply equality checking,
    and the decision audit log all remain owned by the reference layer; this
    module does not modify or duplicate them.

    On ``ACCEPT`` the persisted state must equal ``staged.candidate`` exactly;
    if the guard's independently reconstructed transition ever diverged from
    the staged one, a :class:`Mem0BridgeError` is raised (defensive: the
    mapping is deterministic and the guard fails closed on a changed prior).
    ``HOLD``/``REJECTED`` return the guard decision with no backend write.
    """
    if not isinstance(guard, GuardedMemory):
        raise TypeError("guard must be a membud.adapters.reference.GuardedMemory")
    if not isinstance(staged, StagedProposal):
        raise TypeError("staged must be a StagedProposal")
    decision = guard.propose_update(
        staged.scope_mapping(),
        case_id=staged.case_id,
        candidate=list(staged.transition.candidate),
        operations=[_operation_payload(operation) for operation in staged.transition.operations],
        evidence=list(staged.transition.evidence),
        required_claims=list(staged.transition.required_claims),
        run_id=run_id,
    )
    if decision.decision == "ACCEPT" and decision.transition != staged.transition:
        raise Mem0BridgeError(
            "guarded transition diverged from the staged canonical transition; "
            "this indicates a non-deterministic mapping and must be investigated"
        )
    return decision
