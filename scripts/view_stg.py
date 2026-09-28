#!/usr/bin/env python3
"""tools/view_stg.py — pretty-print a recorded session's State Transition Graph.

Usage:
    python tools/view_stg.py sessions/<session_id>.json
    python tools/view_stg.py sessions/<session_id>.json --full   (include unchanged state each turn)

For each turn (edge) it shows the user utterance, the operations applied,
any failure events, and a diff-style view of what changed in the state
(added / updated / status-changed elements only), so a whole collaboration
can be audited without reading raw JSON.
"""

import json
import sys
import textwrap

_FAILURE_OPS = {
    "extraction_failed",
    "validation_failed",
    "apply_failed",
    "validation_skip",
    "state_invariant_violation",
    "overlap_detected",
    "elements_detached",
    "cross_goal_duplicate",
    "update_lost_content",
    "update_subject_mismatch",
    "type_language_mismatch",
}


def _index(snapshot):
    """Index a state snapshot by element id -> element dict."""
    explicit = snapshot.get("explicit", {})
    return (
        {g["goal_id"]: g for g in explicit.get("goals", [])},
        {v["variable_id"]: v for v in explicit.get("variables", [])},
        {c["constraint_id"]: c for c in explicit.get("constraints", [])},
    )


def _goal_text(g):
    """goal_text for a v2 goal dict; falls back to the v1 version-history format
    so old recorded sessions remain viewable."""
    if g.get("goal_text"):
        return g["goal_text"]
    return next(
        (v["goal_text"] for v in g.get("versions", []) if v["version_id"] == g.get("active_version_id")),
        "?",
    )


def _goal_line(g):
    return f'[{g["goal_id"]}] "{_goal_text(g)}" status={g["status"]}'


def _var_line(v):
    return f'[{v["variable_id"]}] {v["name"]} = {v["value"]!r} role={v["role"]} goal={v["goal_id"]}'


def _con_line(c):
    return f'[{c["constraint_id"]}] {c["name"]}: "{c["description"]}" type={c["constraint_type"]} goal={c["goal_id"]}'


def _diff(prev, curr):
    """Yield human-readable change lines between two snapshots."""
    pg, pv, pc = _index(prev)
    cg, cv, cc = _index(curr)

    for gid, g in cg.items():
        if gid not in pg:
            yield f"+ goal    {_goal_line(g)}"
        elif g != pg[gid]:
            old, new = pg[gid], g
            if old["status"] != new["status"]:
                yield f"~ goal    [{gid}] status {old['status']} -> {new['status']}"
            # v1 sessions only: a version-history change (goals are immutable in v2)
            if old.get("active_version_id") != new.get("active_version_id"):
                yield f'~ goal    [{gid}] text "{_goal_text(old)}" -> "{_goal_text(new)}" (v1 session)'

    for vid, v in cv.items():
        if vid not in pv:
            yield f"+ var     {_var_line(v)}"
        elif v != pv[vid]:
            old = pv[vid]
            changes = [f"{k}: {old[k]!r} -> {v[k]!r}" for k in ("value", "role", "required") if old.get(k) != v.get(k)]
            if changes:
                yield f"~ var     [{vid}] {v['name']}  " + ", ".join(changes)

    for cid, c in cc.items():
        if cid not in pc:
            yield f"+ constr  {_con_line(c)}"
        elif c != pc[cid]:
            old = pc[cid]
            changes = [f"{k}: {old[k]!r} -> {c[k]!r}" for k in ("description", "constraint_type") if old.get(k) != c.get(k)]
            if changes:
                yield f"~ constr  [{cid}] {c['name']}  " + ", ".join(changes)

    # Removals. Without these a turn that DELETES an element looks identical to
    # a turn that changed nothing — and silent loss is the failure mode that
    # matters most here, since no guard fires on it.
    for gid, g in pg.items():
        if gid not in cg:
            yield f"- goal    {_goal_line(g)}"
    for vid, v in pv.items():
        if vid not in cv:
            yield f"- var     {_var_line(v)}"
    for cid, c in pc.items():
        if cid not in cc:
            yield f"- constr  {_con_line(c)}"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    full = "--full" in sys.argv[2:]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"Session {data['session_id']}   started {data.get('started_at', '?')}   model={data.get('model', '?')}")
    if data.get("notes"):
        print(f"Notes: {data['notes']}")
    print(f"Turns: {len(data.get('stg', []))}")

    prev_snapshot = {"explicit": {"goals": [], "variables": [], "constraints": []}}
    failure_count = 0

    for entry in data.get("stg", []):
        print(f"\n{'=' * 62}")
        print(f"{entry['turn_id']}  ({entry.get('timestamp', '?')})")
        print(f'USER: "{entry["user_turn"]}"')

        ops = entry.get("operations", [])
        applied = [op for op in ops if op.get("operation") not in _FAILURE_OPS]
        failures = [op for op in ops if op.get("operation") in _FAILURE_OPS]
        failure_count += len(failures)

        if applied:
            print("  ops:      " + ", ".join(op["operation"] for op in applied))
        for op in failures:
            reason = op.get("reason") or op.get("errors") or ""
            detail = f" — {reason}" if reason else ""
            print(f"  !! {op['operation']} ({op.get('component', 'state')}){detail}")

        # The assistant's reply is what the study actually measures — the state
        # is the mechanism, not the outcome. Printed whenever recorded.
        reply = entry.get("assistant_reply")
        if reply:
            wrapped = textwrap.fill(reply.strip(), width=76,
                                    initial_indent="  ASSISTANT: ",
                                    subsequent_indent="             ",
                                    replace_whitespace=False)
            print("\n".join(wrapped.split("\n")))

        changes = list(_diff(prev_snapshot, entry["state_snapshot"]))
        if changes:
            print("  changes:")
            for line in changes:
                print(f"    {line}")
        else:
            print("  changes:  (none)")

        if full:
            print("  state:")
            print("    " + json.dumps(entry["state_snapshot"], ensure_ascii=False))

        prev_snapshot = entry["state_snapshot"]

    print(f"\n{'=' * 62}")
    print(f"Summary: {len(data.get('stg', []))} turns, {failure_count} failure/warning events")


if __name__ == "__main__":
    main()
