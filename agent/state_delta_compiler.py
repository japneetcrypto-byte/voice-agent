"""Aiva State Delta Compiler — deterministic, no LLM calls.

Receives per-turn deltas from the LLM perception head and maintains
an accumulating entity-relation state across the session.

Handles edge cases: entity correction, supersession, contradiction,
repetition, co-occurrence, emotion resolution, staleness.

Locked boundary: this module NEVER interprets natural language.
It only processes structured delta fields from the perception head.
"""

from __future__ import annotations


class StateDeltaCompiler:
    """Accumulates structured deltas into a session entity-relation map.

    Usage:
        compiler = StateDeltaCompiler()
        compiler.process(turn=1, delta={"entities": [{"name": "Rohit", "relation": "manager"}]})
        compiler.process(turn=2, delta={"entities": [{"name": "Shyam", "replaces": "Rohit"}]})
        state = compiler.get_state()
    """

    def __init__(self):
        self.entities: dict = {}
        self.facts: list = []
        self.emotion_resolution: str | None = None
        self.co_occurrences: dict = {}

    def process(self, turn: int, delta: dict | None) -> dict:
        """Process a single turn's delta. Returns a summary of what changed."""
        changes = {"entities_updated": [], "facts_added": [], "corrections": [], "supersessions": []}
        if not delta:
            return changes

        for ent in delta.get("entities", []):
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            norm = name.lower()
            relation = ent.get("relation", "")
            replaces = (ent.get("replaces") or "").strip().lower()

            if replaces and replaces in self.entities:
                old = self.entities[replaces]
                old["superseded_by"] = norm
                old["active"] = False
                self.entities.setdefault(norm, {"aliases": []})
                self.entities[norm].setdefault("aliases", []).append(old["name"])
                changes["corrections"].append(f"{replaces} -> {norm}")

            if norm in self.entities:
                self.entities[norm]["occurrences"] = self.entities[norm].get("occurrences", 1) + 1
                self.entities[norm]["last_turn"] = turn
                if relation:
                    old_rel = self.entities[norm].get("relation", "")
                    if relation != old_rel:
                        prevs = self.entities[norm].get("previous_relations", [])
                        prevs.append(old_rel)
                        self.entities[norm]["previous_relations"] = prevs
                        self.entities[norm]["relation"] = relation
                        changes["supersessions"].append(f"{norm}: {old_rel} -> {relation}")
                changes["entities_updated"].append(f"{norm} (occurrence {self.entities[norm]['occurrences']})")
            else:
                self.entities[norm] = {
                    "name": name,
                    "relation": relation,
                    "first_turn": turn,
                    "last_turn": turn,
                    "occurrences": 1,
                    "active": True,
                    "aliases": [],
                    "superseded_by": None,
                }
                changes["entities_updated"].append(f"{norm} (new)")

            other_entities = [e.get("name", "").lower() for e in delta.get("entities", [])
                              if e.get("name", "").lower() != norm and e.get("name")]
            for other in other_entities:
                key = tuple(sorted([norm, other]))
                if key not in self.co_occurrences:
                    self.co_occurrences[key] = {"first_turn": turn, "last_turn": turn}
                else:
                    self.co_occurrences[key]["last_turn"] = turn

        fact = delta.get("fact")
        if fact:
            norm_fact = fact.strip().lower()
            is_dup = any(f["content"].lower() == norm_fact and f.get("active", True) for f in self.facts)
            if not is_dup:
                self.facts.append({
                    "content": fact.strip(),
                    "turn": turn,
                    "criterion": delta.get("fact_criterion", "salient"),
                    "active": True,
                    "superseded_by": None,
                })
                changes["facts_added"].append(fact.strip()[:60])

        if delta.get("emotion_resolved"):
            self.emotion_resolution = delta["emotion_resolved"]

        return changes

    def get_state(self) -> dict:
        active_entities = {
            k: v for k, v in self.entities.items()
            if v.get("active", True) and not v.get("superseded_by")
        }
        return {
            "entities": active_entities,
            "facts": [f for f in self.facts if f.get("active", True)],
            "emotion_resolution": self.emotion_resolution,
            "co_occurrences": {f"{a}+{b}": v for (a, b), v in self.co_occurrences.items()},
        }

    def get_memory_candidates(self) -> list:
        candidates = []
        for norm, ent in self.entities.items():
            if not ent.get("active", True):
                continue
            relation = ent.get("relation", "")
            occ = ent.get("occurrences", 1)
            content = ent.get("name", norm)
            if relation:
                content += f" — user's {relation}"
            if occ > 1:
                content += f" (mentioned {occ} times)"
            aliases = ent.get("aliases", [])
            if aliases:
                content += f" (also called: {', '.join(aliases)})"
            candidates.append({
                "type": "relationship",
                "content": content,
                "criterion": "recurrent" if occ > 1 else "salient",
                "occurrences": occ,
            })
        for f in self.facts:
            if f.get("active", True):
                candidates.append({
                    "type": "semantic",
                    "content": f["content"],
                    "criterion": f.get("criterion", "salient"),
                    "occurrences": 1,
                })
        return candidates

    def to_context_string(self) -> str:
        lines = []
        for norm, ent in self.entities.items():
            if not ent.get("active", True):
                continue
            rel = ent.get("relation", "")
            name = ent.get("name", norm)
            lines.append(f"{name} ({rel})" if rel else name)
        return "; ".join(lines) if lines else ""
