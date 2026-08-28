#!/usr/bin/env python3
"""Deterministic regression: state delta compiler (edge cases 1-10)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.state_delta_compiler import StateDeltaCompiler

def test():
    c = StateDeltaCompiler()
    
    # Edge 1+5: New entity + repetition
    c.process(1, {"entities": [{"name": "Rohit", "relation": "manager"}]})
    c.process(2, {"entities": [{"name": "Rohit", "relation": "manager"}]})
    s = c.get_state()
    assert s["entities"]["rohit"]["occurrences"] == 2, "occurrence count wrong"
    print("1. repetition/occurrence count OK")
    
    # Edge 1: Entity correction Ram -> Shyam
    c.process(3, {"entities": [{"name": "Shyam", "relation": "manager", "replaces": "Rohit"}]})
    s = c.get_state()
    assert "shyam" in s["entities"], "Shyam not found"
    assert "rohit" not in s["entities"], "Rohit still active after correction"
    assert c.entities["rohit"]["superseded_by"] == "shyam", "superseded_by not set"
    assert "Rohit" in c.entities["shyam"].get("aliases", []), "alias not added"
    print("2. entity correction (Ram->Shyam) OK, alias preserved")
    
    # Edge 4: Contradictory relation (same entity, new relation)
    c.process(4, {"entities": [{"name": "Shyam", "relation": "ex-manager"}]})
    s = c.get_state()
    assert s["entities"]["shyam"]["relation"] == "ex-manager"
    assert "manager" in s["entities"]["shyam"].get("previous_relations", [])
    print("3. contradictory relation superseded OK")
    
    # Edge 5: Fact repetition
    c.process(5, {"fact": "user has a bread maker"})
    c.process(6, {"fact": "user has a bread maker"})  # duplicate
    s = c.get_state()
    facts = [f for f in s["facts"] if "bread maker" in f["content"].lower()]
    assert len(facts) == 1, f"expected 1 bread maker fact, got {len(facts)}"
    print("4. fact repetition deduped OK")
    
    # Edge 7: Emotion resolution
    c.process(7, {"emotion_resolved": "work frustration resolved"})
    s = c.get_state()
    assert s["emotion_resolution"] == "work frustration resolved"
    print("5. emotion resolution tracked OK")
    
    # Edge 8: Co-occurrence
    c.process(8, {"entities": [{"name": "Rimi", "relation": "spouse"}, {"name": "Gaggu", "relation": "son"}]})
    s = c.get_state()
    cokeys = list(s["co_occurrences"].keys())
    assert any("rimi" in k and "gaggu" in k for k in cokeys), f"co-occurrence missing: {cokeys}"
    print("6. co-occurrence link OK")
    
    # Memory candidates
    mems = c.get_memory_candidates()
    mem_texts = [m["content"] for m in mems]
    assert any("Shyam" in m or "shyam" in m for m in mem_texts), f"Shyam missing from memory: {mem_texts}"
    assert any("Rimi" in m for m in mem_texts), "Rimi missing"
    print("7. memory candidates generated OK")
    
    print("\nALL STATE DELTA TESTS PASS")

test()
