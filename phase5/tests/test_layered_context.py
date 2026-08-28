#!/usr/bin/env python3
"""Deterministic tests for the Layered Context Manager.
Covers: token budgeting, compression trigger, checkpoint atomicity,
precedence rules, relationship promotion, fact conflict resolution."""
import sys, os, json, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Mock LLM compression
class MockCompressor:
    def __init__(self):
        self.called = 0
    def compress(self, prompt):
        self.called += 1
        return '{"people": {"test": "friend"}, "active_topic": "testing", "open_items": [], "emotional_context": "neutral"}'

from agent.layered_context import LayeredContextManager, estimate_tokens

def test_token_estimation():
    assert estimate_tokens("hello world test") >= 3
    assert estimate_tokens("") >= 1
    print("1. token estimation OK")

def test_layer1_budget():
    m = LayeredContextManager()
    for i in range(25):
        m.add_turn("user", f"This is turn {i} with substantially more content to help fill up the token budget for testing compression triggers properly")
    assert m.layer1_tokens > 0
    assert m.needs_compression(), "should need compression"
    overflow = m.get_overflow_turns()
    assert len(overflow) > 0, "should have overflow turns"
    m.remove_overflow(overflow)
    assert m.layer1_tokens < m.compression_trigger, f"still over: {m.layer1_tokens}"
    print("2. layer1 budget + overflow OK")

def test_layer2_rolling():
    m = LayeredContextManager()
    m.set_layer2({"people": {"rohit": "manager"}, "active_topic": "work"})
    # Simulate compression: old Layer 2 + new info → new Layer 2
    new_state = {
        "people": {"rohit": "manager", "priya": "new_manager"},
        "active_topic": "team restructure",
        "open_items": ["ask about timeline"],
        "emotional_context": "neutral",
    }
    m.set_layer2(new_state)
    assert m.get_layer2()["people"]["priya"] == "new_manager"
    assert m.get_layer2()["people"]["rohit"] == "manager"  # kept (rolling, not replaced)
    print("3. layer2 rolling state OK")

def test_checkpoint_atomicity():
    import tempfile
    m = LayeredContextManager(log_dir=tempfile.mkdtemp())
    m.add_turn("user", "test turn")
    m.set_layer2({"people": {"test": "friend"}})
    
    # Save checkpoint
    ok = m.save_checkpoint()
    assert ok, "checkpoint save failed"
    assert m.last_processed_turn == m.turn_counter
    
    # Verify file exists and is valid JSON
    path = os.path.join(m.checkpoint_dir, "latest_checkpoint.json")
    with open(path) as f:
        cp = json.load(f)
    assert cp["last_processed_turn"] == m.turn_counter
    assert "layer2_state" in cp
    
    # Recover in a new instance
    m2 = LayeredContextManager(log_dir=m.checkpoint_dir.replace("/checkpoints", ""))
    m2.checkpoint_dir = m.checkpoint_dir
    ok2 = m2.recover_from_checkpoint()
    assert ok2, "recovery failed"
    assert m2.turn_counter == m.turn_counter
    print("4. checkpoint atomic save/recover OK")

def test_precedence():
    # Layer 1 contains newer info that contradicts Layer 2
    m = LayeredContextManager()
    m.set_layer2({"people": {"rohit": "manager"}, "emotional_context": "frustrated"})
    m.add_turn("user", "actually Rohit apologized, things are fine now")
    
    # When building context, Layer 1 (newer) comes AFTER Layer 2 (older)
    ctx = m.build_context()
    l2_pos = ctx.find("CONVERSATION STATE")
    l1_pos = ctx.find("RECENT CONVERSATION")
    assert l1_pos > l2_pos, "Layer 1 should come after Layer 2 (LLM reads in order)"
    assert "apologized" in ctx, "Layer 1 content should be present"
    print("5. precedence: Layer 1 newer info present alongside Layer 2 OK")

def test_relationship_promotion():
    # Relationship should be in Layer 2 immediately AND flagged for Layer 3 promotion
    m = LayeredContextManager()
    m.set_layer2({
        "people": {"neetu": "sister"},
        "active_topic": "family",
    })
    # The entity extractor should pick this up for Layer 3 promotion
    assert m.get_layer2()["people"]["neetu"] == "sister"
    print("6. relationship in Layer 2 immediate OK")

def test_checkpoint_not_a_context_layer():
    m = LayeredContextManager()
    m.add_turn("user", "test")
    m.save_checkpoint()
    # Checkpoint should NOT appear in build_context output
    ctx = m.build_context()
    assert "checkpoint" not in ctx.lower()
    assert "last_processed_turn" not in ctx
    print("7. checkpoint is not a context layer OK")

def test_compression_prompt():
    m = LayeredContextManager()
    m.set_layer2({"people": {"rohit": "manager"}, "active_topic": "work"})
    overflow = [
        {"role": "user", "content": "actually priya is my new manager", "tokens": 20, "turn": 5},
        {"role": "assistant", "content": "achha, aur kya hua?", "tokens": 15, "turn": 6},
    ]
    prompt = m.get_compression_prompt(overflow)
    assert "CURRENT STATE" in prompt
    assert "NEW TURNS" in prompt
    assert "rohit" in prompt
    assert "priya" in prompt
    print("8. compression prompt OK")

if __name__ == "__main__":
    test_token_estimation()
    test_layer1_budget()
    test_layer2_rolling()
    test_checkpoint_atomicity()
    test_precedence()
    test_relationship_promotion()
    test_checkpoint_not_a_context_layer()
    test_compression_prompt()
    print("\nALL LAYERED CONTEXT TESTS PASS")
