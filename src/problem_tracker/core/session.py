"""core/session.py

Session — persistence and the State Transition Graph (STG).

A Session records a complete collaboration as the sequence
(S_0, (u_1, L_1, S_1), (u_2, L_2, S_2), ...): the formal transition record of
Chapter 3. Nodes are TURN-BASED (one STG entry per user turn, including turns
that changed nothing — "nothing changed" is itself evidence when evaluating
extraction), matching the formal model in which the update U is defined per
turn. Goal changes need no special node status: they are visible as operations
on the edge.

Each STG entry stores:
    turn_id         "t<N>"
    timestamp       ISO-8601, second precision
    user_turn       the raw utterance u_t (the edge label; without it the
                    transition record cannot be evaluated against the dialogue)
    operations      the full operation log L_t for the turn, INCLUDING failure
                    events (extraction_failed, validation_skip, apply_failed,
                    overlap_detected, state_invariant_violation) so failure
                    rates are measurable from recorded sessions
    state_snapshot  pd.to_dict() taken AFTER the turn (the node S_t). Snapshots
                    are plain dicts produced by dataclasses.asdict, so they are
                    decoupled from the live state and later mutation cannot
                    corrupt them.

The layer is purely observational: it never mutates the ProblemDescription and
never influences the updaters.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Any

from problem_tracker.core.problem_description import ProblemDescription

__all__ = ["SESSION_FORMAT_VERSION", "Session", "prompt_fingerprint"]

SESSION_FORMAT_VERSION = "session_v1"


def prompt_fingerprint() -> str:
    """Short hash of every prompt and few-shot currently in use.

    Recorded in each session so that a change to extraction or reply behaviour
    is visible in the data. The study's paired design keeps each participant's
    own comparison valid under whatever prompts were live at the time, but
    aggregating across participants requires knowing who ran under which
    version — this makes that checkable rather than remembered.
    """
    import problem_tracker.prompt_specs as ps
    parts = [
        ps.GOAL_SYSTEM_PROMPT,
        ps.VARIABLE_EXTRACTION_SYSTEM_PROMPT,
        ps.CONSTRAINT_EXTRACTION_SYSTEM_PROMPT,
        repr(ps._FEW_SHOTS),
        repr(ps._VARIABLE_EXTRACTION_FEW_SHOTS),
        repr(ps._CONSTRAINT_FEW_SHOTS),
    ]
    try:
        from problem_tracker.study.reply import _PD_PREAMBLE, REPLY_SYSTEM_PROMPT
        parts += [REPLY_SYSTEM_PROMPT, _PD_PREAMBLE]
    except Exception:
        pass  # reply generation is only present for study runs
    return hashlib.sha256(chr(31).join(parts).encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_session_id() -> str:
    # timestamp for human sorting + short random suffix against collisions
    return f"s{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class Session:
    def __init__(
        self,
        session_id: str | None = None,
        pd: ProblemDescription | None = None,
        model: str = "",
        notes: str = "",
        decode: dict[str, Any] | None = None,
        store_content: bool = True,
    ):
        # store_content=False writes NO participant-derived text: no utterance,
        # no assistant reply, no state snapshot, and operation entries reduced to
        # their type. What remains — turn count, operation types, event types and
        # element counts — is not personal data and still supports the
        # log-derived failure-rate measure.
        #
        # Probe and replay runs keep the default (True): those turns are written
        # by the researcher, are not personal data, and are the evidence base for
        # RQ1/RQ2.
        self.store_content = store_content
        self.session_id = session_id or _new_session_id()
        self.started_at = _now()
        self.model = model      # e.g. the LLM used, for later comparison across runs
        # Decode settings (temperature, seed). Recorded because they change the
        # variance of the recorded state as much as the prompts do, and a
        # prompt fingerprint alone would not reveal a change to them.
        self.decode = dict(decode or {})
        self.notes = notes      # free-text label, e.g. scenario name / participant id
        self.prompt_version = prompt_fingerprint()
        self.pd = pd or ProblemDescription()
        self.stg: list[dict[str, Any]] = []
        self._turn_counter = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def next_turn_id(self) -> str:
        self._turn_counter += 1
        return f"t{self._turn_counter}"

    def record_turn(
        self,
        turn_id: str,
        user_turn: str,
        operations: list[dict[str, Any]],
        pd: ProblemDescription,
    ) -> dict[str, Any]:
        """Append one STG entry (low-level; state must already be updated)."""
        if self.store_content:
            entry = {
                "turn_id": turn_id,
                "timestamp": _now(),
                "user_turn": user_turn,
                "operations": operations,
                "state_snapshot": pd.to_dict(),
            }
        else:
            # Derived record only. Operation entries carry element names and
            # before/after values, both derived from what the participant typed,
            # so only the type of each operation survives.
            entry = {
                "turn_id": turn_id,
                "timestamp": _now(),
                "operations": [
                    {k: op[k] for k in ("operation", "component") if k in op}
                    for op in operations
                ],
                "state_size": {
                    "goals": len(pd.explicit.goals),
                    "active_goals": len(pd.active_goals()),
                    "variables": len(pd.explicit.variables),
                    "constraints": len(pd.explicit.constraints),
                },
            }
        self.stg.append(entry)
        self.pd = pd
        return entry

    def process_turn(self, updater, user_turn: str) -> dict[str, Any]:
        """Convenience wrapper: run one turn through an ExplicitUpdater and
        record it. Returns the updater result (state + operation_log)."""
        turn_id = self.next_turn_id()
        result = updater.update(self.pd, user_turn, turn_id)
        self.pd = result["state"]
        self.record_turn(turn_id, user_turn, result["operation_log"], self.pd)
        return result

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data = {
            "format": SESSION_FORMAT_VERSION,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "model": self.model,
            "decode": self.decode,
            "prompt_version": self.prompt_version,
            "notes": self.notes,
            "stg": self.stg,
            "stores_content": self.store_content,
        }
        # The final state is built from what the participant said, so it is
        # withheld along with the per-turn snapshots.
        if self.store_content:
            data["final_state"] = self.pd.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        session = cls(
            session_id=data["session_id"],
            pd=ProblemDescription.from_dict(data.get("final_state", {})),
            model=data.get("model", ""),
            notes=data.get("notes", ""),
            decode=data.get("decode"),
            store_content=data.get("stores_content", True),
        )
        session.started_at = data.get("started_at", session.started_at)
        # keep the RECORDED version, not the one current at load time
        session.prompt_version = data.get("prompt_version", "unknown")
        session.stg = list(data.get("stg", []))
        # Restore the turn counter from the last recorded turn id ("t<N>").
        if session.stg:
            last = session.stg[-1].get("turn_id", "t0")
            digits = last[1:] if last[:1] == "t" else "0"
            session._turn_counter = int(digits) if digits.isdigit() else len(session.stg)
        return session

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def save(self, directory: str = "sessions") -> str:
        """Write the session to <directory>/<session_id>.json. Returns the path."""
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str) -> Session:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
