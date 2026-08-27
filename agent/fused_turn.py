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

from agent.prompt_fragments import (
    SYSTEM_FUSED_V11, SYSTEM_PLAIN_V11,
    FILLER_LINES, PRESENCE_LINES_D7, OPENDOOR_LINES_D8, pick_line,
)

TAG_RE = re.compile(r"<perception>(.*?)</perception>", re.DOTALL)


class FusedLLM:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("AIVA_LLM_MODEL", "gemini-3.5-flash-lite")
        self._client = None
        self.head: dict | None = None
        self.meta: dict = {}

    def _client_for(self, key: str):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=key)
        return self._client

    def _degraded_system(self) -> str:
        return SYSTEM_PLAIN_V11

    def build_contents(self, user_text: str, policy: dict, memory_view: list,
                       threads: list, history: list) -> str:
        return json.dumps({
            "policy": policy,
            "memory": memory_view,
            "threads": threads,
            "history": history,
            "user_turn": user_text,
        }, ensure_ascii=False)

    async def stream_prose(self, *, user_text: str, turn_type: str, policy: dict,
                            memory_view: list, threads: list, history: list,
                            turn_no: int, degraded: bool,
                            key: str) -> AsyncGenerator[str, None]:
        """Yields spoken prose. Sets self.head / self.meta for the updater."""
        self.head, self.meta = None, {}
        self.meta["turn_type"] = turn_type

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
        contents = self.build_contents(user_text, policy, memory_view, threads, history)
        config = {"temperature": 0.7, "system_instruction": system}
        client = self._client_for(key)

        attempt, prose_started, spoken_any = 0, False, False
        while True:
            buf, emitted = "", 0
            t0 = time.perf_counter()
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    txt = chunk.text or ""
                    if not txt:
                        continue
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
                                self.meta["head_raw_snippet"] = m.group(1).strip()[:200]
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
                    # D2 missing head: the full stream is prose (C7 D2); degraded passthrough
                    piece = buf[emitted:].strip()
                    if piece:
                        spoken_any = True
                        yield piece
                    emitted = len(buf)
                if not spoken_any:
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
                if "429" in str(e) and attempt == 0 and not prose_started:
                    attempt += 1
                    await asyncio.sleep(65)   # audit rule: retry once, zero-prose only
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
                full = buf
                m = TAG_RE.search(full)
                if m and self.head is None:
                    try:
                        self.head = json.loads(m.group(1).strip())
                    except json.JSONDecodeError:
                        try:
                            obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                            self.head = obj if isinstance(obj, dict) else None
                        except json.JSONDecodeError:
                            self.head = None
