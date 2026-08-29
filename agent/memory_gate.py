"""MemoryGate — store-level validation for every memory write (2026-08-29).

Owner critique: "beating around the bush; blast radius not contained; will
fail in extreme situations again." Correct: extraction bugs wrote straight
into the owner's LIVE memory ('गए — user's bhai'), because validation lived
at each call site (extractor regexes, promotion policy) and the store accepted
everything it was handed.

This gate is the LAST LINE OF DEFENSE: MemoryStore.commit() routes EVERY
candidate through it before touching the database. Even if an upstream
extractor is buggy, the worst case is a QUARANTINED row (invisible, auditable,
purgeable) — never live-context pollution.

Verdicts:
  reject      -> structural garbage; not stored at all (logged)
  quarantine  -> suspicious; stored as status='quarantined' (invisible to
                 view()/promotion, kept for incident analysis)
  pending     -> first legitimate sighting; invisible until confirmed
  commit      -> repeated/confirmed fact request (store still dedupes)

Deterministic, stdlib-only. Rules are ordered; first match wins.
"""
from __future__ import annotations

from agent.entity_extractor import USER_STOPWORDS

MIN_CONTENT_CHARS = 6
MIN_NAME_CHARS = 2


def _letters(s: str) -> int:
    return sum(1 for c in s if c.isalpha() or "\u0900" <= c <= "\u097F")


def _name_of(content: str) -> str:
    """Relationship contents are 'Name — user's relation'."""
    return content.split("—")[0].strip() if "—" in content else content.strip()


def gate_candidate(candidate: dict) -> tuple[str, str]:
    """Return (verdict, reason). verdict ∈ {commit, pending, quarantine, reject}."""
    typ = (candidate.get("type") or "semantic").strip()
    content = (candidate.get("content") or "").strip()
    criterion = (candidate.get("criterion") or "salient").strip()
    immediate = bool(candidate.get("immediate", False))

    # R1 — degenerate content: nothing learnable
    if len(content) < MIN_CONTENT_CHARS or _letters(content) < 3:
        return "reject", f"degenerate content ({content[:30]!r})"

    # R2 — relationship candidates: the name must look like a name
    if typ == "relationship":
        name = _name_of(content)
        if len(name) < MIN_NAME_CHARS:
            return "quarantine", f"name too short ({name!r})"
        if name.lower() in USER_STOPWORDS:
            return "quarantine", f"name is a stopword/verb ({name!r})"
        if not _letters(name):
            return "quarantine", f"name has no letters ({name!r})"
        # repeated+confirmed relationship -> commit; first sighting -> pending
        if criterion == "explicit" and immediate:
            return "commit", "relationship repeat-confirmed"
        return "pending", "relationship first sighting"

    # R3 — other types: structural floor only (policy layers handle cadence)
    if criterion == "explicit" and immediate:
        return "commit", f"{typ} explicit+immediate"
    return "pending", f"{typ} first sighting"
