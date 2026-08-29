"""aiva.transport — fused perception + response turn (Phase 5, 5.1 + 5.4).

Implements C4 (transport byte-shape), C7 (degradation D1/D2/D4/D4b/D9/D7/D8),
A-U7 (correction head field) against gemini via google-genai, mirroring the
Task-1-validated call pattern from providers/llm.py.

The stream_prose() async generator yields ONLY spoken prose chunks (the
perception head is stripped and exposed via .head / .meta after the stream).
Degradation never restarts mid-streamed audio (D4b); LLM failure with zero
prose yields the deterministic D4 filler (U1 wording draft — pending approval).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import AsyncGenerator

from agent.reply_guard import smart_join
from agent.prompt_fragments import (
    SYSTEM_FUSED_V11, SYSTEM_PLAIN_V11, PROMPT_VERSION,
    FILLER_LINES, PRESENCE_LINES_D7, OPENDOOR_LINES_D8, pick_line,
    BACKCHANNEL_LINES, LISTEN_LINES, CLARIFY_LINES,
)

# Evidence 2026-08-29 (session 091548 t30/t33): flash-lite sometimes closes
# the head with '</p>' (HTML habit) or never closes it at all. Accept both;
# the unclosed case is salvaged by salvage_unclosed_head() below.
# Class-level fix (evidence t8 session 103824, 4 variants so far:
# /perception, /p, /parception, /s_perception, /s:perception): accept ANY
# short closing tag — prose never contains angle brackets (persona forbids
# special chars), so this cannot misfire on real replies.
TAG_RE = re.compile(r"<perception>(.*?)</[^>]{1,24}>", re.DOTALL)
OPEN_TAG = "<perception>"


def salvage_unclosed_head(buf: str) -> tuple[dict | None, str]:
    """Recover from '<perception>{...}prose...' that never got closed.

    Returns (head, speakable_tail). The raw tag region is NEVER speakable:
    if the JSON parses, the head is recovered and the tail after it is spoken;
    if it does not parse, the whole region is dropped (nothing before the tag
    is lost)."""
    idx = buf.find(OPEN_TAG)
    if idx == -1:
        return None, buf
    before = buf[:idx]
    rest = buf[idx + len(OPEN_TAG):].strip()
    try:
        obj, end = json.JSONDecoder().raw_decode(rest)
        if isinstance(obj, dict):
            from agent.reply_guard import strip_tag_leak
            tail = strip_tag_leak((before + rest[end:]))[0]
            return obj, tail.strip()
    except json.JSONDecodeError:
        pass
    return None, before.strip()


MODEL_POOL = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

def _all_keys():
    """All available Gemini API keys."""
    keys = []
    primary = os.getenv("GEMINI_API_KEY", "")
    if primary and not primary.startswith(("your_", "<<<")):
        keys.append(primary)
    for i in (2, 3, 4):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if k and not k.startswith(("your_", "<<<")):
            keys.append(k)
    return keys if keys else [""]


class FusedLLM:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("AIVA_LLM_MODEL", "gemini-3.5-flash-lite")
        self._client = None
        self.head: dict | None = None
        self.meta: dict = {}
        # Monotonic per-call id: stream_prose() resets .meta on every call, so a
        # concurrent reader (e.g. the previous turn finishing playback during a
        # barge-in) could read the NEXT turn's meta or an empty dict. Readers
        # snapshot this epoch before streaming and only trust .meta if it still
        # matches afterwards.
        self.epoch: int = 0

    def _client_for(self, key: str):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=key)
        return self._client

    def _degraded_system(self) -> str:
        return SYSTEM_PLAIN_V11

    def build_contents(self, user_text: str, policy: dict, memory_view: list,
                       threads: list, history: list, layer2: dict | None = None) -> str:
        payload = {
            "policy": policy,
            "memory": memory_view,
            "threads": threads,
            "history": history,
            "user_turn": user_text,
        }
        # Layer 2 (compressed session state) — only when it carries content,
        # per the approved 3-layer design (docs/LAYERED_CONTEXT_ARCHITECTURE.md).
        if layer2 and (layer2.get("people") or layer2.get("open_items")
                       or layer2.get("emotional_context")):
            payload["session_state"] = layer2
        return json.dumps(payload, ensure_ascii=False)

    async def stream_prose(self, *, user_text: str, turn_type: str, policy: dict,
                            memory_view: list, threads: list, history: list,
                            turn_no: int, degraded: bool, layer2: dict | None = None,
                            key: str) -> AsyncGenerator[str, None]:
        """Yields spoken prose. Sets self.head / self.meta for the updater."""
        self.head, self.meta = None, {}
        self.epoch += 1
        self.meta["turn_type"] = turn_type

        # Turn-taking (owner brief): backchannels get 1-3 word acknowledgments;
        # listen requests get one short listening line. No LLM call — the policy
        # decision is deterministic (structured turn_relation flag -> policy goal).
        # P0 low-confidence STT: deterministic clarification, no LLM, no invention
        if turn_type == "unclear_speech":
            line = pick_line(CLARIFY_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "unclear_speech"})
            yield line
            return

        goal = (policy or {}).get("response_goal")
        if goal == "backchannel":
            line = pick_line(BACKCHANNEL_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "backchannel"})
            yield line
            return
        if goal == "listen_quietly":
            line = pick_line(LISTEN_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "listen_request"})
            yield line
            return

        # D7 / D8: no LLM call — deterministic lines (updater policy already encodes these)
        if turn_type == "acoustic_only":
            line = pick_line(PRESENCE_LINES_D7, turn_no)
            self.meta.update({"degradation": "D7", "llm_called": False})
            yield line
            return
        if turn_type == "idle":
            policy = policy or {}
            if policy.get("response_suppressed"):
                self.meta.update({"degradation": "D8", "llm_called": False, "suppressed": True})
                return
            line = pick_line(OPENDOOR_LINES_D8, turn_no)
            self.meta.update({"degradation": "D8", "llm_called": False})
            yield line
            return

        system = self._degraded_system() if degraded else SYSTEM_FUSED_V11
        import hashlib
        self.meta["prompt_version"] = PROMPT_VERSION
        self.meta["system_sha1"] = hashlib.sha1(system.encode()).hexdigest()[:10]
        contents = self.build_contents(user_text, policy, memory_view, threads, history,
                                       layer2=layer2)
        self.meta["context"] = contents
        config = {"temperature": 0.7, "system_instruction": system}

        # Rotation: (key × model) pairs — 3 keys × 2 models = 6 attempts on 429
        keys = _all_keys()
        rotations = [(k, m) for k in keys for m in MODEL_POOL]
        rot_idx = 0
        attempt, prose_started, spoken_any = 0, False, False

        while True:
            buf, emitted = "", 0
            t0 = time.perf_counter()
            active_key, active_model = rotations[rot_idx % len(rotations)] if rotations else (key, self.model)
            client = self._client_for(active_key)
            self.meta["active_model"] = active_model
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=active_model,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    txt = chunk.text or ""
                    if not txt:
                        continue
                    # Restore spaces lost at API chunk boundaries (only after
                    # the head closed — inserting inside the head JSON would
                    # corrupt values). Evidence 103824 t2/t3: 'aaram sebaithne',
                    # 'saathchalna' were SPOKEN as merged tokens.
                    if prose_started:
                        buf = smart_join(buf, txt)
                    else:
                        buf += txt
                    self.meta.setdefault("ttft_s", round(time.perf_counter() - t0, 3))
                    m = TAG_RE.search(buf)
                    if m and not prose_started:
                        # head closed — everything after the tag is prose
                        prose_started = True
                        self.meta["head_complete_s"] = round(time.perf_counter() - t0, 3)
                        try:
                            self.head = json.loads(m.group(1).strip())
                        except json.JSONDecodeError:
                            try:
                                obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                                self.head = obj if isinstance(obj, dict) else None
                            except json.JSONDecodeError:
                                self.head = None  # D1: prose passthrough, no state update
                                self.meta["head_raw_snippet"] = m.group(1).strip()[:400]
                                self.meta["head_fail_class"] = "invalid_json"
                    if prose_started:
                        start = max(m.end(), emitted) if m else emitted
                        if len(buf) > start:
                            piece = buf[start:]
                            emitted = len(buf)
                            if not spoken_any:
                                piece = piece.lstrip()   # drop newlines right after </perception>
                            if piece:
                                spoken_any = True
                                yield piece
                    elif degraded and not prose_started:
                        # degraded mode: no head expected — everything is prose
                        prose_started = True
                        piece = buf[emitted:].lstrip()
                        if piece:
                            spoken_any = True
                            yield piece
                        emitted = len(buf)
                if not prose_started and buf.strip():
                    # D2 missing head: keep the raw for diagnosis. AUDIT-FIX
                    # 2026-08-29: the raw tag region must NEVER reach TTS
                    # (session 091548 t30/t33 spoke '<perception>{...}' aloud).
                    self.meta["head_raw_snippet"] = buf.strip()[:400]
                    if OPEN_TAG in buf:
                        self.meta["head_fail_class"] = "unclosed_tags"
                        head2, tail = salvage_unclosed_head(buf)
                        if head2 is not None:
                            if self.head is None:
                                self.head = head2
                            self.meta["head_fail_class"] = "unclosed_recovered"
                        piece = tail[emitted:] if len(tail) > emitted else tail.strip()
                        piece = piece.strip()
                    else:
                        self.meta["head_fail_class"] = "missing_tags"
                        piece = buf[emitted:].strip()
                    if piece:
                        spoken_any = True
                        yield piece
                    emitted = len(buf)
                if not spoken_any:
                    self.meta.setdefault("head_raw_snippet", buf.strip()[:400])
                    self.meta.setdefault("head_fail_class", "head_only_no_prose" if self.head else "empty_stream")
                    # nothing spoken: head-only response, or truly empty stream.
                    # Silence is a contract violation — deterministic fallback speaks.
                    self.meta["empty_prose_fallback"] = True
                    tail = ""
                    m2 = TAG_RE.search(buf)
                    if m2:
                        tail = buf[m2.end():].strip()
                    if tail:
                        yield tail
                    else:
                        yield pick_line(FILLER_LINES, turn_no)
                break  # success — D4b: never restart after the stream finished
            except Exception as e:
                if "429" in str(e) and not prose_started:
                    attempt += 1
                    if attempt < len(rotations):
                        rot_idx = attempt
                        ak, am = rotations[rot_idx]
                        print(f"[LLM] 429 — rotating to key/model #{rot_idx+1}: {am}")
                        continue
                    else:
                        print(f"[LLM] all {len(rotations)} combos exhausted — cooling 65s")
                        await asyncio.sleep(65)
                        rot_idx, attempt = 0, 0
                        continue
                self.meta["llm_failed"] = True
                self.meta["llm_error"] = f"{type(e).__name__}: {str(e)[:150]}"
                if not prose_started:
                    # D4: deterministic filler (U1 wording approved 2026-08-26)
                    self.meta["degradation"] = "D4"
                    yield pick_line(FILLER_LINES, turn_no)
                # D4b: >=1 complete sentence already streamed -> stop cleanly
                return
            finally:
                # D1/D2 final head extraction (even on D4b partial streams)
                if self.head is None and "head_fail_class" not in self.meta:
                    # no class recorded anywhere -> stream ended before the
                    # head completed (cancel/empty). Name it, don't say "unknown".
                    self.meta["head_fail_class"] = "head_never_completed"
                full = buf
                m = TAG_RE.search(full)
                if m and self.head is None:
                    self.meta["head_raw_snippet"] = m.group(1).strip()[:400]
                    try:
                        self.head = json.loads(m.group(1).strip())
                    except json.JSONDecodeError:
                        try:
                            obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                            self.head = obj if isinstance(obj, dict) else None
                            if self.head is not None:
                                self.meta["head_fail_class"] = "trailing_data_rawdecode_recovered"
                        except json.JSONDecodeError as e2:
                            self.head = None
                            self.meta["head_fail_class"] = f"invalid_json: {e2}"
