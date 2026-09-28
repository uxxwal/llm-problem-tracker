from problem_tracker.core.session import Session
from problem_tracker.llm_client import LLMClient
from problem_tracker.updaters import ExplicitUpdater


turns = [
    "Help me plan a trip to Kyoto.",
    "I want to stay for 5 days and go in spring.",
    "Actually, make it a cycling trip.",
    "Forget the trip — I need help writing my CV instead.",
]

client = LLMClient()
updater = ExplicitUpdater(client=client)
session = Session(model=client.model, notes="batch: kyoto->cv demo")

for turn in turns:
    result = session.process_turn(updater, turn)
    print(f"\n--- {session.stg[-1]['turn_id']}: {turn!r} ---")
    print("Operations:", [op["operation"] for op in result["operation_log"]])

print("\nFinal state:", session.pd.to_dict())
path = session.save()
print(f"\nSession saved: {path}")
print(f"View it with:  python tools/view_stg.py {path}")
