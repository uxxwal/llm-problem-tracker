#!/usr/bin/env python3
"""tools/check_llm.py — pre-flight check for the configured LLM endpoint.

Run this before a study session. It verifies, in order:
  1. the environment is configured
  2. the endpoint answers a plain chat call
  3. it returns parseable JSON for an extraction call
  4. a full pipeline turn runs end to end

    python tools/check_llm.py

Exits non-zero on the first failure, so it is safe to run as a gate.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from problem_tracker.config import load_env
from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.llm_client import LLMClient
from problem_tracker.study.reply import ReplyGenerator
from problem_tracker.updaters import ExplicitUpdater


def fail(msg):
    print(f"  FAIL  {msg}")
    sys.exit(1)


def main():
    print("1. environment")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if load_env(os.path.join(root, ".env")):
        print("  ok    loaded .env")
    base = os.getenv("LLM_BASE_URL")
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not base:
        fail("LLM_BASE_URL is not set")
    if not key:
        fail("LLM_API_KEY is not set")
    if not model:
        print("  WARN  LLM_MODEL not set — falling back to the client default")
    print(f"  ok    base={base}")
    print(f"  ok    model={model or '(default)'}")
    print(f"  ok    key set ({len(key)} chars, not shown)")

    client = LLMClient()

    print("\n2. plain chat call")
    try:
        out = client.chat([{"role": "user", "content": "Reply with the single word: ready"}])
    except Exception as e:
        fail(f"chat call raised {type(e).__name__}: {e}\n"
             f"        If this is an auth or 404 error, the endpoint may not be\n"
             f"        OpenAI-compatible and llm_client.py needs adapting.")
    if not out or not out.strip():
        fail("chat call returned an empty response")
    print(f"  ok    replied: {out.strip()[:60]!r}")

    print("\n3. JSON extraction call")
    try:
        ops = client.chat_json([
            {"role": "system", "content": "Reply with ONLY a JSON array of objects. No markdown."},
            {"role": "user", "content": 'Return exactly: [{"operation": "no_goal_change"}]'},
        ])
    except Exception as e:
        fail(f"chat_json raised {type(e).__name__}: {e}")
    if not isinstance(ops, list):
        fail(f"chat_json returned {type(ops).__name__}, expected list — "
             f"the model may not follow JSON instructions reliably")
    print(f"  ok    parsed: {ops}")

    print("\n4. full pipeline turn")
    try:
        updater = ExplicitUpdater(client=client)
        result = updater.update(ProblemDescription(), "I want to plan a weekend away in Skye "
                                                      "for under 400 pounds", "t1")
        pd = result["state"]
        ops = [o["operation"] for o in result["operation_log"]]
    except Exception as e:
        fail(f"pipeline raised {type(e).__name__}: {e}")
    print(f"  ok    operations: {ops}")
    print(f"  ok    goals={len(pd.explicit.goals)} "
          f"variables={len(pd.explicit.variables)} "
          f"constraints={len(pd.explicit.constraints)}")
    if not pd.is_valid():
        fail(f"resulting state failed validation: {pd.validate()}")
    if not pd.explicit.goals:
        print("  WARN  no goal extracted from a clear goal statement — check the model "
              "is strong enough for the study")

    print("\n5. reply generation (both arms)")
    try:
        gen = ReplyGenerator(client=client)
        history = [{"role": "user", "content": "I want to plan a weekend away in Skye"}]
        base_reply = gen.reply(history, "baseline")
        pd_reply = gen.reply(history, "problem_description", pd)
    except Exception as e:
        fail(f"reply generation raised {type(e).__name__}: {e}")
    print(f"  ok    baseline reply: {base_reply.strip()[:70]!r}")
    print(f"  ok    schema reply:   {pd_reply.strip()[:70]!r}")

    print("\nAll checks passed — safe to run study/app.py")


if __name__ == "__main__":
    main()
