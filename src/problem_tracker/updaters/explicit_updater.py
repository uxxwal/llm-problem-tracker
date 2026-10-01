"""updaters/explicit_updater.py

ExplicitUpdater: per-turn orchestrator for the explicit state pipeline.

Pipeline per turn:
  1. ExplicitGoalUpdater.update()     — goal ops (create/complete/abandon/no-change;
                                        goals are immutable, goal_schema_v2)
  2. ExplicitVariableUpdater.update() — variable extraction per focal goal
  3. ExplicitConstraintUpdater.update() — constraint extraction per focal goal
  4. pd.validate()                    — invariant check; failures recorded in log

Goals may be active concurrently, so a turn can have multiple focal goals:
every goal created this turn, else the most-recently-updated active goal.
Each focal goal gets its own variable/constraint extraction pass.

All log entries use the key ``turn_id`` (a string such as ``"t1"``).
"""

from __future__ import annotations

import logging
from typing import Any

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.explicit_constraint_updater import (
    ExplicitConstraintUpdater,
)
from problem_tracker.updaters.explicit_goal_updater import ExplicitGoalUpdater
from problem_tracker.updaters.explicit_variable_updater import ExplicitVariableUpdater

logger = logging.getLogger(__name__)


def _focal_goal_ids_for_turn(
    goal_log: list[dict[str, Any]], pd: ProblemDescription
) -> list[str]:
    """Return the goal_ids this turn's variable/constraint extraction is scoped to.

    Goals are immutable and may be active concurrently, so a turn can have
    MULTIPLE focal goals (e.g. an abandon+create pivot that also states details
    of the new objective, or a turn that creates two goals at once).

    Preference order:
    1. Every goal CREATED this turn (from goal_log), in creation order — a
       turn that introduces objectives usually also states their details.
    2. Otherwise ALL active goals. With concurrent goals, no structural signal
       says which objective a detail-only turn refers to ("make it a cycling
       trip" while both a trip and a packing list are active), so routing is
       delegated to extraction: each pass sees the goal's text and existing
       elements and is expected to return nothing for goals the turn does not
       concern. Recency was tried first and misroutes exactly this case.
    3. Empty — no active goal at all.
    """
    created = [
        entry["goal_id"] for entry in goal_log
        if entry.get("operation") == "create_goal"
    ]
    # keep only goals that are still active after the whole turn (a created
    # goal could in principle be completed in the same turn)
    created_active = [gid for gid in created
                      if (g := pd.get_goal(gid)) is not None and g.status == "active"]
    if created_active:
        return created_active

    return [g.goal_id for g in pd.active_goals()]


# Obligation vs preference language. Used only to DETECT a mismatch between how
# a constraint is worded and how it was typed — never to retype it, because the
# wording is sometimes the wrong signal ("must have an indoor backup if the
# weather is bad" is a genuine hard constraint carrying a condition).
_HARD_MARKERS = frozenset({
    "must", "cannot", "cant", "need", "needs", "required", "have", "has",
    "never", "no", "not", "only", "definitely", "absolutely",
})
_SOFT_MARKERS = frozenset({
    "ideally", "prefer", "prefers", "preferably", "preferred", "around",
    "roughly", "approximately", "about", "possible", "possibly", "rather",
    "nice", "hopefully", "flexible", "acceptable", "could",
})


def _content_tokens(text: str) -> set:
    return {t for t in _norm(text).split() if len(t) > 2}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Jaccard overlap of content tokens, for near-duplicate detection.

    Exact and determiner-normalised comparison both miss the observed case:
    "must submit the visa application six weeks before travel" against "visa
    application must be submitted six weeks before travel" — same condition,
    reordered, one inflection apart. Set overlap sees it.

    The threshold is a declared parameter, not a tuned one: report it. This
    stays deterministic (no embeddings, no second model call), so the
    "model proposes, code disposes" property is preserved.
    """
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return False
    # Quantities are decisive, not just one token among many. "must not exceed
    # 900" and "must not exceed 200" overlap on everything except the number,
    # which is the entire difference — set overlap alone would merge them and
    # silently delete one of two real limits.
    na = {t for t in ta if any(ch.isdigit() for ch in t)}
    nb = {t for t in tb if any(ch.isdigit() for ch in t)}
    if na != nb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def _contains_tokens(haystack: str, needle: str) -> bool:
    """True when every token of ``needle`` appears contiguously in ``haystack``."""
    h, n = _norm(haystack).split(), _norm(needle).split()
    if not n:
        return False
    return any(h[i:i + len(n)] == n for i in range(len(h) - len(n) + 1))


def _detect_overlap(
    variable_ops: list[dict[str, Any]],
    constraint_ops: list[dict[str, Any]],
    turn_id: str,
) -> list[dict[str, Any]]:
    """Flag same-turn variable/constraint pairs holding the same information.

    Under the formal separation (variables state what IS; constraints state what
    must HOLD) a single utterance should land in exactly one store.

    TWO SIGNALS, and the second is the reliable one:

    - a shared name token (variable ``budget`` vs constraint ``budget_ceiling``);
    - the variable's VALUE appearing verbatim inside the constraint's
      description (``pickup_time = "4pm"`` vs "must pick up my kid by 4pm").

    Name-token matching alone was too loose in both directions: it fired on
    ``sister_call`` vs ``call_sister`` where the two hold different information,
    and missed ``due_date`` vs ``submission_deadline`` where they hold the same.
    Pairs matching on VALUE are duplicates the constraint strictly dominates, and
    are marked resolvable; pairs matching on name alone are only reported.
    """
    entries: list[dict[str, Any]] = []
    variables = [op for op in variable_ops
                 if op.get("operation") in ("create_variable", "update_variable")]
    constraints = [op for op in constraint_ops
                   if op.get("operation") in ("create_constraint", "update_constraint")]

    for v in variables:
        v_name = v["name"]
        v_value = str((v.get("after") or {}).get("value") or "")
        for c in constraints:
            c_name = c["name"]
            c_desc = (c.get("after") or {}).get("description", "")
            shared_name = bool(set(v_name.split("_")) & set(c_name.split("_")))
            shared_value = bool(v_value) and _contains_tokens(c_desc, v_value)
            if not (shared_name or shared_value):
                continue
            entries.append({
                "operation": "overlap_detected",
                "variable_name": v_name,
                "constraint_name": c_name,
                "matched_on": "value" if shared_value else "name",
                "resolvable": shared_value and v.get("operation") == "create_variable",
                "variable_id": v.get("variable_id"),
                "turn_id": turn_id,
            })
    return entries


_STOPWORDS = frozenset({
    "a", "an", "the", "my", "our", "your", "their", "his", "her", "its",
    "this", "that", "these", "those",
    # "per" only ever alternates with "a" in rate expressions ("£900 per month"
    # / "£900 a month"), and two genuinely different conditions will not differ
    # by this word alone.
    "per",
})


def _norm(text: str) -> str:
    """Normalise a description for duplicate comparison.

    Two extraction passes over the SAME turn routinely produce the same
    statement with a determiner swapped — "must pick up my kid by 4" for one
    goal and "must pick up the kid by 4" for another. Exact-string comparison
    treats those as distinct and both survive.

    This strips case, punctuation and a closed list of determiners and
    possessives. It is deliberately NOT semantic: no embeddings, no second
    model call. The deterministic half of the pipeline stays deterministic, so
    the cost is that genuine paraphrase ("must finish by 5pm" vs "everything
    has to be done by five") still slips through. That residue is L3, and is
    reported rather than hidden.
    """
    tokens = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.lower()).split()
    return " ".join(t for t in tokens if t not in _STOPWORDS)


class ExplicitUpdater:
    def __init__(self, client=None) -> None:
        self.goal_updater = ExplicitGoalUpdater(client=client)
        self.variable_updater = ExplicitVariableUpdater(client=client)
        self.constraint_updater = ExplicitConstraintUpdater(client=client)

    @staticmethod
    def _affinity(pd, goal_id: str, content: str) -> int:
        """How many content words a goal's TEXT shares with an element's content.

        Used to decide WHICH copy of a duplicate to keep. Keeping the first
        arrival means keeping whichever goal happens to have the lower id, which
        is creation order and nothing more: on `concurrent` that put the visa
        deadline on "Plan move" and left "Sort out visa" holding nothing about a
        visa. Overlap between "visa application must be submitted six weeks
        before travel" and "Sort out visa" is a weak signal, but it is a signal,
        and it beats an arbitrary one.
        """
        goal = pd.get_goal(goal_id)
        if goal is None:
            return 0
        return len(_content_tokens(goal.goal_text) & _content_tokens(content))

    @staticmethod
    def _remove_element(pd, kind, element_id):
        store = pd.explicit.variables if kind == "variable" else pd.explicit.constraints
        id_field = f"{kind}_id"
        for i, el in enumerate(store):
            if getattr(el, id_field) == element_id:
                store.pop(i)
                return True
        return False

    @classmethod
    def _drop_cross_goal_duplicates(cls, pd, ops, claimed, kind, turn_id, operation_log):
        """Keep ONE copy of each piece of content created this turn.

        ``claimed`` maps a content key to the copy currently kept, so a later
        focal goal with a BETTER claim on the content can take it over rather
        than simply losing: whichever goal's text overlaps the content more keeps
        it, and ties fall to the first arrival.
        """
        kept = []
        for op in ops:
            if op.get("operation") != f"create_{kind}":
                kept.append(op)
                continue
            after = op.get("after", {})
            content = (_norm(str(after.get("value", ""))) if kind == "variable"
                       else _norm(after.get("description", "")))
            keys = {(after.get("name", ""), content)}
            if kind == "constraint" and content:
                # Also key on the CONDITION alone. The same requirement restated
                # for a second goal often arrives under a different name
                # (`visa_application_deadline` on one goal, `application_deadline`
                # on another), which a name-bearing key cannot see. What makes two
                # constraints the same is the condition, not the label.
                keys.add((None, content))
            # Find an existing claim: exact key, or — for constraints — a
            # reworded restatement. Exact keys miss "must submit the visa
            # application six weeks before travel" against "visa application
            # must be submitted six weeks before travel", which is how one
            # deadline reached three goals.
            prior = next((claimed[k] for k in keys if k in claimed), None)
            if prior is None and kind == "constraint" and content:
                prior = next((rec for k, rec in claimed.items()
                              if k[1] and _similar(content, k[1])), None)

            mine = cls._affinity(pd, op.get("goal_id"), content)

            if prior is not None:
                loser_is_prior = mine > prior["affinity"]
                if loser_is_prior:
                    # This goal has the better claim on the content: take it over.
                    cls._remove_element(pd, kind, prior["element_id"])
                    dropped_name, dropped_goal = prior["name"], prior["goal_id"]
                    prior.update(element_id=op.get(f"{kind}_id"), name=after.get("name", ""),
                                 goal_id=op.get("goal_id"), affinity=mine)
                    kept.append(op)
                else:
                    cls._remove_element(pd, kind, op.get(f"{kind}_id"))
                    dropped_name, dropped_goal = after.get("name", ""), op.get("goal_id")
                logger.warning("[ExplicitUpdater] turn %s: dropped duplicate %s %r on goal %s",
                               turn_id, kind, dropped_name, dropped_goal)
                operation_log.append({
                    "operation": "cross_goal_duplicate",
                    "component": kind,
                    "name": dropped_name,
                    "goal_id": dropped_goal,
                    "kept_on": prior["goal_id"],
                    "turn_id": turn_id,
                })
            else:
                record = {"element_id": op.get(f"{kind}_id"), "name": after.get("name", ""),
                          "goal_id": op.get("goal_id"), "affinity": mine}
                for k in keys:
                    claimed[k] = record
                kept.append(op)
        return kept

    # NOTE — deliberately NOT resolved in the pipeline. Removing the redundant
    # variable was tried and reverted: at `scheduling` t3 it deleted
    # `mechanic_availability`, and at t6, when the appointment moved, the model
    # had no mechanic variable left to update and wrote 2pm into
    # `fixed_meeting_time` instead — destroying the 11am fact. The redundancy is
    # LOAD-BEARING: it gives a later update somewhere to land. Duplication is
    # therefore removed at RENDER time (study/reply.py), where it costs nothing,
    # and the recorded state stays complete for maintenance.

    @staticmethod
    def _detect_subject_mismatch(pd, variable_ops, user_turn, goal_id, turn_id):
        """Flag an update to a variable the turn never mentions, when a DIFFERENT
        tracked variable is clearly the one being talked about.

        The most damaging observed error: "actually the mechanic can't do the
        morning, it'll have to be 2pm" updated `fixed_meeting_time` to 2pm,
        destroying the 11am fact, while `mechanic_availability` sat there
        untouched. The result is a confident false statement about the user's
        situation, produced by a VALID operation on a REAL variable — so no
        existing guard sees it.

        The signal is comparative, not absolute: silence is normal ("actually
        let's double that" names nothing), so a bare miss is not suspicious. It
        is only suspicious when some other variable on the same goal DOES match
        the turn and was left alone. That asymmetry keeps false positives low.
        """
        turn_tokens = _content_tokens(user_turn)
        entries = []
        for op in variable_ops:
            if op.get("operation") != "update_variable":
                continue
            target = op.get("name", "")
            if set(target.split("_")) & turn_tokens:
                continue  # the turn names its subject — nothing to see
            # Rivals include CONSTRAINTS, not just variables. Replaying this
            # guard over the recorded sessions showed it silent on all four runs
            # where the misroute actually happened: the turn's real subject was
            # tracked only as a constraint (`mechanic_availability`), never as a
            # variable, so a variables-only search found no rival and stayed
            # quiet on precisely the cases it exists for.
            rivals = [
                el.name for el in (list(pd.explicit.variables) + list(pd.explicit.constraints))
                if el.goal_id == goal_id and el.name != target
                and set(el.name.split("_")) & turn_tokens
            ]
            if not rivals:
                continue  # the turn names nothing tracked; silence is normal
            logger.warning("[ExplicitUpdater] turn %s: updated %r, which the turn does "
                           "not mention, while %s does appear", turn_id, target, rivals)
            entries.append({
                "operation": "update_subject_mismatch",
                "component": "variable",
                "name": target,
                "mentioned_instead": rivals,
                "turn_id": turn_id,
            })
        return entries

    @staticmethod
    def _detect_type_language_mismatch(constraint_ops, turn_id):
        """Flag constraints whose wording and type disagree.

        Soft constraints are routinely phrased with obligation language
        ("must grow vegetables...", typed soft), which renders to the reply model
        as "Preferred (soft constraints): must ..." and muddies the very
        distinction the injection exists to convey. Across three runs the
        hard/soft type was also the ONLY thing that varied on two probes.

        Detection only — the type is never rewritten, because wording is an
        unreliable signal in the other direction ("must have an indoor backup if
        the weather is bad" is genuinely hard). This exists to make the
        hard/soft confusion countable, which is what the evaluation needs.
        """
        entries = []
        for op in constraint_ops:
            if op.get("operation") not in ("create_constraint", "update_constraint"):
                continue
            after = op.get("after") or {}
            desc, ctype = after.get("description", ""), after.get("constraint_type")
            tokens = set(_norm(desc).split())
            hard, soft = tokens & _HARD_MARKERS, tokens & _SOFT_MARKERS
            mismatch = ((ctype == "soft" and hard and not soft)
                        or (ctype == "hard" and soft and not hard))
            if not mismatch:
                continue
            entries.append({
                "operation": "type_language_mismatch",
                "component": "constraint",
                "name": op.get("name"),
                "constraint_type": ctype,
                "markers": sorted(hard or soft),
                "turn_id": turn_id,
            })
        return entries

    @staticmethod
    def _detect_lost_content(operation_log, turn_id):
        """Flag constraint updates that silently drop the quantity they constrained.

        Rewriting a value is normal ("no more than 30" -> "no more than 25").
        Replacing a quantified rule with an unquantified one is not: the
        `research` probe turns "nothing older than about 2015" into "older papers
        are acceptable if they are widely cited", losing the rule it was meant to
        qualify. One `update_constraint`, no failure event, invisible.

        The signal is deliberately narrow to keep false positives near zero: the
        old description contained numbers and the new one contains none. This
        does not repair anything — it makes a silent loss OBSERVABLE, which is
        the gap that makes log-derived error rates flatter the system.
        """
        entries = []
        for op in operation_log:
            if op.get("operation") != "update_constraint":
                continue
            before = (op.get("before") or {}).get("description", "")
            after = (op.get("after") or {}).get("description", "")
            old_nums = {t for t in _norm(before).split() if any(ch.isdigit() for ch in t)}
            new_nums = {t for t in _norm(after).split() if any(ch.isdigit() for ch in t)}
            if old_nums and not new_nums:
                logger.warning("[ExplicitUpdater] turn %s: update to %r dropped %s and "
                               "added no quantity", turn_id, op.get("name"), sorted(old_nums))
                entries.append({
                    "operation": "update_lost_content",
                    "component": "constraint",
                    "name": op.get("name"),
                    "lost": sorted(old_nums),
                    "before": before,
                    "after": after,
                    "turn_id": turn_id,
                })
        return entries

    def update(self, pd: ProblemDescription, user_turn: str, turn_id: str) -> dict[str, Any]:
        operation_log: list[dict[str, Any]] = []

        # Stage 1: goal ops — routed through ExplicitGoalUpdater for validation
        # and before/after snapshots. Failures are recorded in goal_log, not raised.
        pd, goal_log = self.goal_updater.update(pd, user_turn, turn_id)
        operation_log.extend(goal_log)

        # Guard: ending a goal detaches every variable and constraint recorded
        # under it. That is intended when the user genuinely abandons an
        # objective, and destructive when the model misreads an addition or a
        # detail change as a pivot. Either way it must be countable, not silent.
        for entry in goal_log:
            if entry.get("operation") not in ("abandon_goal", "complete_goal"):
                continue
            gid = entry["goal_id"]
            lost_vars = [v.name for v in pd.explicit.variables if v.goal_id == gid]
            lost_cons = [c.name for c in pd.explicit.constraints if c.goal_id == gid]
            if lost_vars or lost_cons:
                logger.warning(
                    "[ExplicitUpdater] turn %s: goal %s ended with %d variable(s) and "
                    "%d constraint(s) still attached; they are now out of scope",
                    turn_id, gid, len(lost_vars), len(lost_cons),
                )
                operation_log.append({
                    "operation": "elements_detached",
                    "component": "goal",
                    "goal_id": gid,
                    "variables": lost_vars,
                    "constraints": lost_cons,
                    "turn_id": turn_id,
                })

        # Stage 2 & 3: variable and constraint extraction, scoped to the
        # goal(s) actually touched by this turn. With concurrent goals a turn
        # can have several focal goals; each gets its own extraction pass so
        # details attach to the objective they belong to.
        # Text already claimed by an earlier focal goal this turn. With several
        # goals active each gets its own extraction pass, and passes tend to
        # claim the same utterance for every goal ("prefer no-dig beds" landing
        # on both the allotment and the fruit-tree goal). A statement belongs to
        # one goal; later duplicates are dropped and logged.
        claimed_variables: dict[Any, dict[str, Any]] = {}
        claimed_constraints: dict[Any, dict[str, Any]] = {}

        for focal_goal_id in _focal_goal_ids_for_turn(goal_log, pd):
            pd, variable_ops = self.variable_updater.update(pd, user_turn, focal_goal_id, turn_id)
            variable_ops = self._drop_cross_goal_duplicates(
                pd, variable_ops, claimed_variables, "variable", turn_id, operation_log)

            pd, constraint_ops = self.constraint_updater.update(pd, user_turn, focal_goal_id, turn_id)
            constraint_ops = self._drop_cross_goal_duplicates(
                pd, constraint_ops, claimed_constraints, "constraint", turn_id, operation_log)

            # Guard: the same information landing as both a variable and a
            # constraint in one turn violates the variable/constraint
            # separation. Value-matched pairs are resolved (the variable goes);
            # name-matched pairs are only reported, since a shared token does
            # not establish that the content is the same.
            operation_log.extend(self._detect_subject_mismatch(
                pd, variable_ops, user_turn, focal_goal_id, turn_id))
            operation_log.extend(self._detect_type_language_mismatch(constraint_ops, turn_id))

            overlaps = _detect_overlap(variable_ops, constraint_ops, turn_id)
            for entry in overlaps:
                logger.warning(
                    "[ExplicitUpdater] variable/constraint overlap on turn %s: %s vs %s",
                    turn_id, entry["variable_name"], entry["constraint_name"],
                )
                operation_log.append(entry)

            operation_log.extend(variable_ops)
            operation_log.extend(constraint_ops)

        # Guard: an update that removes the quantity a constraint carried, and
        # replaces it with nothing, is a silent loss — the one failure class
        # nothing else in the pipeline observes.
        operation_log.extend(self._detect_lost_content(operation_log, turn_id))

        # Stage 4: invariant check — log violations rather than raising.
        errors = pd.validate()
        if errors:
            logger.error("[ExplicitUpdater] state invariant violation after turn %s: %s", turn_id, errors)
            operation_log.append({
                "operation": "state_invariant_violation",
                "turn_id": turn_id,
                "errors": errors,
            })

        return {"state": pd, "operation_log": operation_log}
