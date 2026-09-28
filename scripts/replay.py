#!/usr/bin/env python3
"""tools/replay.py — re-run a set of turns through the current pipeline.

Two uses:

1. Replay a RECORDED session against the current prompts, to see whether a
   prompt change fixed what you meant it to fix:

       python tools/replay.py sessions/s2026....json

2. Replay turns typed inline, for quick probing:

       python tools/replay.py -t "help me find a place in Leith" -t "a bar with food"

Add --runs 3 to repeat the same turns several times: since application is
deterministic, any difference between runs is extraction nondeterminism, which
is the inter-run consistency measure for RQ2.

Nothing is written to sessions/ by default — this is an analysis tool, not a
study run. Pass --save to record the run as a session so it can be inspected
turn by turn:

    python tools/replay.py --probe scheduling --save
    python tools/view_stg.py sessions/probes/<id>.json

Replay runs go to sessions/probes/, never sessions/ — the latter is the study
corpus and must not be mixed with synthetic runs.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from problem_tracker.config import load_env, require_llm_config            # noqa: E402
from problem_tracker.core.problem_description import ProblemDescription    # noqa: E402
from problem_tracker.llm_client import LLMClient                           # noqa: E402
from problem_tracker.updaters import ExplicitUpdater                       # noqa: E402
from problem_tracker.study.reply import render_problem_description         # noqa: E402

_FAILURE_OPS = {
    "extraction_failed", "validation_failed", "apply_failed",
    "validation_skip", "overlap_detected", "state_invariant_violation",
    "elements_detached", "cross_goal_duplicate", "update_lost_content",
    "update_subject_mismatch", "type_language_mismatch",
}



def turns_from_session(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [e["user_turn"] for e in data.get("stg", [])]


def run_once(turns, updater, verbose=True, session=None):
    """Replay turns. If a Session is given, each turn is recorded to its STG so
    the run can be inspected turn by turn with tools/view_stg.py."""
    pd = session.pd if session is not None else ProblemDescription()
    failures = []
    for i, text in enumerate(turns, 1):
        if session is not None:
            result = session.process_turn(updater, text)
        else:
            result = updater.update(pd, text, f"t{i}")
        pd = result["state"]
        ops = result["operation_log"]
        failures += [o for o in ops if o["operation"] in _FAILURE_OPS]
        if verbose:
            print(f"\n[t{i}] {text}")
            applied = [o["operation"] for o in ops if o["operation"] not in _FAILURE_OPS]
            print("     ops:", ", ".join(applied) if applied else "(none)")
            for o in ops:
                if o["operation"] in _FAILURE_OPS:
                    reason = o.get("reason") or o.get("errors") or ""
                    print(f"     !! {o['operation']}"
                          f"{' — ' + str(reason) if reason else ''}")
    return pd, failures


def fingerprint(pd):
    """Comparable summary of a final state, for run-to-run comparison.

    TEXT-SENSITIVE: two runs that record the same problem in different words
    count as different here. DESCRIPTIONS ARE INCLUDED — they are what gets
    injected into the reply model, so a version that compared only names would
    report "identical" while the text the participant is actually affected by
    still varied.
    """
    return {
        "goals": sorted((g.goal_text, g.status) for g in pd.explicit.goals),
        "variables": sorted((v.goal_id, v.name, str(v.value)) for v in pd.explicit.variables),
        "constraints": sorted((c.goal_id, c.name, c.constraint_type, c.description)
                              for c in pd.explicit.constraints),
    }


def structure_signature(pd):
    """SHAPE of a final state, ignoring what things are called.

    Two kinds of run-to-run difference matter very differently:

      LEXICAL   — the same problem recorded under different names
                  (guest_count / group_size / attendee_count). The state says
                  the same thing; only the labels moved.
      STRUCTURAL— a different number of goals, a different split of elements
                  between the stores, or different values. The state says
                  something ELSE.

    Reporting a single identical/differ verdict conflates them, and since names
    are freely generated the verdict is almost always "differ" — which hides
    the finding rather than showing it. This signature keeps counts, types and
    VALUES (values are user-supplied, so they are stable when extraction is)
    while dropping names entirely.
    """
    per_goal = []
    for g in sorted(pd.explicit.goals, key=lambda g: g.goal_id):
        variables = [v for v in pd.explicit.variables if v.goal_id == g.goal_id]
        constraints = [c for c in pd.explicit.constraints if c.goal_id == g.goal_id]
        per_goal.append({
            "status": g.status,
            "n_variables": len(variables),
            "variable_values": sorted(str(v.value) for v in variables),
            "constraint_types": sorted(c.constraint_type for c in constraints),
        })
    return {"n_goals": len(pd.explicit.goals), "goals": per_goal}


def main():
    from problem_tracker.study import probes as _probes
    from problem_tracker.study import held_out as _held

    class probes:
        """Probe lookup across both sets. Held-out conversations are kept in a
        separate module so it stays obvious which were used during development
        and which were not; --probe all covers the development set only, so a
        held-out run has to be asked for by name."""
        @staticmethod
        def get(name):
            return _held.get(name) if name in _held.HELD_OUT else _probes.get(name)
        @staticmethod
        def names():
            return _probes.names()
        @staticmethod
        def all_names():
            return _probes.names() + _held.names()

    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="a sessions/*.json file to replay")
    ap.add_argument("-t", "--turn", action="append", default=[], help="a turn (repeatable)")
    ap.add_argument("--probe", help="a probe set, or 'all' for the development set. "
                                    f"development: {', '.join(_probes.names())}. "
                                    f"held-out: {', '.join(_held.names())}")
    ap.add_argument("--runs", type=int, default=1, help="repeat N times to check consistency")
    ap.add_argument("--save", action="store_true",
                    help="record to sessions/ so the run can be viewed with tools/view_stg.py")
    args = ap.parse_args()

    if args.probe == "all":
        load_env(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
        require_llm_config()
        updater = ExplicitUpdater(client=LLMClient())
        for name in probes.names():
            probe = probes.get(name)
            print(f"\n\n{'#' * 70}\n# PROBE: {name}\n# {probe['why']}\n{'#' * 70}")
            shapes = []
            for run in range(1, args.runs + 1):
                if args.runs > 1:
                    print(f"\n{'=' * 66}\nRUN {run}/{args.runs}\n{'=' * 66}")
                pd, failures = run_once(probe["turns"], updater)
                print(f"\n{'-' * 66}\nFINAL STATE\n{'-' * 66}")
                print(render_problem_description(pd))
                print(f"\n{len(failures)} failure/warning event(s)")
                shapes.append(structure_signature(pd))
            if args.runs > 1:
                same = all(s == shapes[0] for s in shapes)
                print("\nSTRUCTURAL CONSISTENCY:",
                      "same shape across runs" if same else "RUNS DIFFER IN SHAPE")
            print("\nCHECK:")
            for w in probe["watch"]:
                print(f"  [ ] {w}")
        return

    if args.probe:
        probe = probes.get(args.probe)
        turns = probe["turns"]
        print(f"PROBE: {args.probe}\n{probe['why']}\n")
    else:
        turns = args.turn or (turns_from_session(args.session) if args.session else [])
    if not turns:
        ap.error("give a session file, a --probe, or at least one --turn")

    load_env(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    require_llm_config()
    client = LLMClient()
    updater = ExplicitUpdater(client=client)

    print(f"Replaying {len(turns)} turns through model={client.model}")

    prints = []
    shapes = []
    for run in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n{'=' * 66}\nRUN {run}/{args.runs}\n{'=' * 66}")
        session = None
        if args.save:
            from problem_tracker.core.session import Session
            label = args.probe or (os.path.basename(args.session) if args.session else "adhoc")
            session = Session(model=client.model, decode=client.describe(),
                              notes=f"replay:{label}" + (f" run {run}" if args.runs > 1 else ""))
        pd, failures = run_once(turns, updater, verbose=True, session=session)
        print(f"\n{'-' * 66}\nFINAL STATE\n{'-' * 66}")
        print(render_problem_description(pd))
        print(f"\n{len(failures)} failure/warning event(s)")
        if session is not None:
            # NOT sessions/ — that directory is the study corpus and must not be
            # contaminated with synthetic replay runs.
            path = session.save("sessions/probes")
            print(f"saved {path}   ->  python tools/view_stg.py {path}")
        prints.append(fingerprint(pd))
        shapes.append(structure_signature(pd))

    if args.probe:
        print("\nCHECK:")
        for w in probes.get(args.probe)["watch"]:
            print(f"  [ ] {w}")

    if args.runs > 1:
        same_names = all(p == prints[0] for p in prints)
        same_shape = all(s == shapes[0] for s in shapes)
        print(f"\n{'=' * 66}")

        # Structural agreement is the one that matters: it asks whether the runs
        # recorded the SAME PROBLEM, not whether they chose the same words for it.
        if same_shape:
            print("STRUCTURAL CONSISTENCY: same shape across runs "
                  "(same goals, same split between stores, same values)")
        else:
            print("STRUCTURAL CONSISTENCY: RUNS DIFFER IN SHAPE — the runs "
                  "recorded different problems")
            for s in sorted({json.dumps(s, sort_keys=True) for s in shapes}):
                print("   ", s)

        if same_names:
            print("TEXTUAL CONSISTENCY:    identical names AND descriptions across runs")
        else:
            print("TEXTUAL CONSISTENCY:    wording differs across runs"
                  + (" — same shape, different words" if same_shape else ""))
            for kind in ("goals", "variables", "constraints"):
                seen = {json.dumps(p[kind]) for p in prints}
                if len(seen) > 1:
                    print(f"  {kind} differed across runs:")
                    for s in sorted(seen):
                        print("   ", s)

        if same_shape and not same_names:
            print("\n  NOTE: names are the identity mechanism for updates and "
                  "descriptions are what\n  reaches the reply model, so textual "
                  "instability is not free — but it is a\n  different failure "
                  "from recording a different problem.")


if __name__ == "__main__":
    main()
