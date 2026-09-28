#!/usr/bin/env python3
"""tests/run_tests.py — pytest-compatible function-style test runner for environments
without pytest installed. Run from the project root with: python3 tests/run_tests.py

When pytest is available, use: pytest tests/ -v  (preferred)
"""
import importlib
import os
import sys
import traceback
import types

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_MODULES = [
    "tests.test_schemas",
    "tests.test_prompt_specs",
    "tests.test_applicators",
    "tests.test_goal_updater",
    "tests.test_variable_updater",
    "tests.test_constraint_updater",
    "tests.test_explicit_updater",
    "tests.test_session",
    "tests.test_study",
]


def run():
    passed = failed = 0
    for mod_name in TEST_MODULES:
        mod = importlib.import_module(mod_name)
        fns = [
            (name, obj)
            for name, obj in vars(mod).items()
            if name.startswith("test_") and callable(obj) and isinstance(obj, types.FunctionType)
        ]
        print(f"\n{'─' * 60}")
        print(f"  {mod_name}")
        print(f"{'─' * 60}")
        for name, fn in fns:
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception:
                print(f"  FAIL  {name}")
                traceback.print_exc(limit=4)
                failed += 1

    total = passed + failed
    print(f"\n{'═' * 60}")
    print(f"  {passed}/{total} passed", end="")
    if failed:
        print(f"  ·  {failed} FAILED")
    else:
        print("  ·  all green")
    print(f"{'═' * 60}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
