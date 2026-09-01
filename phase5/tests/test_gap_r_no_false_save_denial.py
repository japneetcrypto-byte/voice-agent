#!/usr/bin/env python3
"""GAP R (owner 2026-09-01) — the agent must never falsely claim it cannot
save/remember. Evidence: session_20260831_202922 t5/t6 — with mem=25 items in
context, the LLM answered 'देख लिये होगा तेरे पास' with 'address save toh main
nahi kar sakta yaar, system mein nahi hota' / 'mere paas system mein save nahi
hota na kuch' — a FALSE capability denial (the system DOES save confirmed
numbers and explicit facts).

Fix (prompt-level, NOT a rail):
1. prompt_fragments rule 14 gains the NEVER-DENY clause: never claim 'main
   save nahi kar sakta' / 'system mein kuch nahi hota'; if THIS specific
   thing has no record, say 'hmm, yaad nahi hai — batao na'.
2. fused_turn.build_contents injects a capability-honesty memory_note when
   the user's turn is save/remember-intent AND memory is non-empty (the
   empty-memory note stays for recall/empty). Pure recall queries with
   memory present keep NO note (multisession pin preserved: the fact may be
   in memory — the note would be noise).

Lifecycle slot: RECALL (honest use of memory).

Run: python3 phase5/tests/test_gap_r_no_false_save_denial.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.fused_turn import FusedLLM
from agent.prompt_fragments import PERSONA

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

MEM = ["fact: user: मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे (explicit)",
       "saved number: user's mobile number: 9935411907 (explicit)"]

print("== prompt: rule 14 NEVER-DENY clause is pinned ==")
check("rule 14 still pinned (yaad nahi)", "yaad nahi" in PERSONA, True)
check("rule 14 still pinned (NEVER INVENT RECALL)", "NEVER INVENT RECALL" in PERSONA, True)
check("NEVER-DENY clause present in prompt", "save nahi kar sakta" in PERSONA, True)
check("NEVER-DENY names the system capability", "DO save" in PERSONA, True)

print("\n== build_contents: save/remember-intent + memory present -> capability note ==")
p1 = FusedLLM().build_contents("देख लिये होगा तेरे पास", {"mode": "CALM"}, MEM, [], [])
check("save-status query -> memory_note present", '"memory_note"' in p1, True)
check("note forbids the false denial", "save nahi kar sakta" in p1, True)
check("note affirms the capability", "DO save" in p1, True)
p2 = FusedLLM().build_contents("याद रखना है आपके पास", {"mode": "CALM"}, MEM, [], [])
check("'याद रखना' -> note present", '"memory_note"' in p2, True)
p3 = FusedLLM().build_contents("मैंने तुझे अपना नंबर सेप करवाया था", {"mode": "CALM"}, MEM, [], [])
check("past-tense save query -> note present", '"memory_note"' in p3, True)

print("\n== preserved pins (multisession acceptance) ==")
p4 = FusedLLM().build_contents("मैंने कौन सी जगह बताई थी?", {"mode": "CALM"}, MEM, [], [])
check("recall query + memory present -> NO memory_note (pin kept)", '"memory_note"' not in p4, True)
p5 = FusedLLM().build_contents("मैंने कौन सी जगह बताई थी?", {"mode": "CALM"}, [], [], [])
check("recall query + empty memory -> note present (pin kept)", '"memory_note"' in p5, True)

print("\n== no false positives on neutral turns ==")
p6 = FusedLLM().build_contents("आज क्या कर रहे हो", {"mode": "CALM"}, MEM, [], [])
check("neutral turn + memory present -> NO note", '"memory_note"' not in p6, True)
p7 = FusedLLM().build_contents("मौसम कैसा है आज", {"mode": "CALM"}, MEM, [], [])
check("weather chit-chat -> NO note", '"memory_note"' not in p7, True)

print("\n== save-intent + empty memory -> note present ==")
p8 = FusedLLM().build_contents("देख लिये होगा तेरे पास", {"mode": "CALM"}, [], [], [])
check("save-status query + empty memory -> note present", '"memory_note"' in p8, True)

print("\n== 2026-09-01: honest line must not end with 'batao na' / ask back ==")
check("rule 14 no longer ends with 'batao na'", "yaad nahi hai — batao na" not in PERSONA, True)
check("honest line still present", "yaad nahi hai" in PERSONA, True)
check("never-ask-back clause present", "kitne bataye" in PERSONA, True)
p9 = FusedLLM().build_contents("देख लिये होगा तेरे पास", {"mode": "CALM"}, MEM, [], [])
check("capability note no longer ends with 'batao na'", "yaad nahi hai — batao na" not in p9, True)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)
