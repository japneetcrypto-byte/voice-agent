"""Ack Bridge v2 — Fish-voiced, semantically-selected acknowledgment clips.

Owner directives (2026-08-30):
  1. "use fish audio for both" — acks must use the CLONED Fish voice.
     Edge-voiced acks created the jarring same-sentence voice flip
     ("theek hai" in Microsoft Madhur, reply in the clone).
  2. "the word being spoken should make sense" — the ack is DERIVED from
     the turn's semantics each turn (question / venting / positive /
     neutral), never a random pick that sounds out of the blue.

Mechanism:
  - At worker startup, pre-synthesize the ack pool via Fish Audio
    (one-time cost), CACHED TO DISK (logs/acks_cache/) so restarts do not
    re-burn Fish quota.
  - If Fish is unavailable at startup, acks are DISABLED (silence). An ack
    in a different voice is exactly the jarring experience this replaces;
    acks are optional latency-fillers, so disabled > wrong-voice. (Reply
    TTS keeps its Edge fallback per owner sign-off — this is ack-only.)

  The gap-filling role is unchanged: user stops -> ack (cached PCM, zero
  latency) -> brief gap -> full reply.
"""
from __future__ import annotations

import hashlib
import os
import re

import numpy as np

# ---------------------------------------------------------------------------
# Semantically-derived ack pool (deterministic; pick rotates by turn number —
# the project's pick_line discipline: no randomness).
# ---------------------------------------------------------------------------
ACK_POOL = {
    # User asked something: acknowledge — never an imperative "batao/bolo"
    # filler (owner 2026-09-01: "remove btao as a filler that we have added";
    # the old 'achha, batao' clip also opened like a reply to "hello").
    "question": ["haan, kya hua?", "haan", "achha, samjha"],
    # User is venting / distressed: attentive listening cues, NOT "theek hai".
    "venting": ["hmm", "achha", "haan, samajh raha hoon"],
    # User is happy / excited: match the energy lightly.
    "positive": ["haan haan", "achha achha", "haan"],
    # Default: neutral acknowledgment.
    "neutral": ["achha", "haan", "hmm"],
}

# Semantic cues (deterministic regex — no LLM, no interpretation engine).
_QUESTION_RE = re.compile(
    r"\?|kya|kaise|kyon|kab|kahan|kaun|kis|क्या|कैसे|क्यों|कब|कहाँ|कौन|किस"
    r"|how|why|what|when|where|who|can you|could you|ho sakta|sakta hai",
    re.IGNORECASE)
_NEGATIVE_RE = re.compile(
    r"gussa|dukh|pareshaan|pareshan|thak|problem|musibat|tension|chinta|rona"
    r"|dar|akela|afso|buri|bura|ganda|behuda|sad|angry|upset|hurt|depress|bore"
    r"|nahi chal|गुस्सा|दुख|परेशान|थक|टेंशन|चिंता|रोना|डर|अकेला|बुरा|बुरी|नहीं चल",
    re.IGNORECASE)
_POSITIVE_RE = re.compile(
    r"khush|badhiya|accha laga|achha laga|maza|great|awesome|nice|happy|exciting"
    r"|बढ़िया|खुश|अच्छा लगा|मज़ा",
    re.IGNORECASE)

# Turn relations that must NOT get an ack: the deterministic policy already
# answers them (backchannel -> 1-3 word line; listen_request -> listening
# line; the user asked for silence, an ack would be wrong).
_NO_ACK_RELATIONS = {"listen_request", "backchannel", "empty"}


def pick_ack_for(text: str, turn_relation: str, turn_no: int) -> tuple[str | None, str | None]:
    """Derive the acknowledgment word for THIS turn.

    Returns (word|None, reason|None). Deterministic: same (text, relation,
    turn_no) -> same word. reason is the semantic category for telemetry.
    """
    if turn_relation in _NO_ACK_RELATIONS:
        return None, f"no_ack:{turn_relation}"
    t = (text or "").strip()
    if not t:
        return None, "no_ack:empty"
    if _QUESTION_RE.search(t):
        cat = "question"
    elif _NEGATIVE_RE.search(t):
        cat = "venting"
    elif _POSITIVE_RE.search(t):
        cat = "positive"
    else:
        cat = "neutral"
    pool = ACK_POOL[cat]
    return pool[turn_no % len(pool)], cat


class AckBridge:
    """Pre-synthesized Fish ack clips + semantic selection at play time."""

    def __init__(self, cache_dir: str = "logs/acks_cache"):
        self._clips: dict[str, np.ndarray] = {}  # word -> int16 PCM @48kHz mono
        self._ready = False
        self.cache_dir = cache_dir
        self._cache_stamp = None

    @property
    def ready(self) -> bool:
        return self._ready and len(self._clips) > 0

    def _cache_path(self, word: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", word.lower()).strip("_")
        return os.path.join(self.cache_dir, f"{self._cache_stamp}_{slug}.npy")

    def _load_cache(self) -> set[str]:
        """Load ANY cached ack clips from disk. Returns the set of words
        loaded (partial cache is fine — missing words get synthesized)."""
        if not self._cache_stamp:
            return set()
        loaded = set()
        for w in {w for pool in ACK_POOL.values() for w in pool}:
            try:
                clip = np.load(self._cache_path(w))
                if clip.size > 0:
                    self._clips[w] = clip
                    loaded.add(w)
            except Exception:
                pass
        return loaded

    async def pregenerate(self) -> None:
        """One-time: synthesize the ack pool with the CLONED Fish voice.

        Disk cache first (no quota burn on restart). Fish unavailable ->
        acks disabled (silence), never a different voice.
        """
        voice_id = os.getenv("FISH_AUDIO_REFERENCE_ID", "")
        self._cache_stamp = hashlib.sha1(
            (voice_id + "|ack-v2").encode()).hexdigest()[:10]
        os.makedirs(self.cache_dir, exist_ok=True)

        cached = self._load_cache()
        if cached == {w for pool in ACK_POOL.values() for w in pool}:
            self._ready = True
            print(f"[AckBridge] loaded {len(self._clips)} cached clips "
                  f"(voice {voice_id[:8]})")
            return

        from providers.tts import FishAudioTTSProvider
        try:
            fish = FishAudioTTSProvider()
        except (ValueError, ImportError) as e:
            print(f"[AckBridge] Fish unavailable — acks DISABLED (no wrong-voice "
                  f"acks): {e}")
            if self._clips:
                self._ready = True  # partial cache still usable
            return

        words = {w for pool in ACK_POOL.values() for w in pool}
        for w in sorted(words):
            if w in self._clips:
                continue
            clip = await self._synth_one(fish, w)
            if (clip is None or len(clip) < 500) and len(w) <= 4:
                # Retry once with a trailing period — Fish can be picky with
                # ultra-short single words (owner: 'filler words did not
                # speak out', 2026-08-30).
                print(f"[AckBridge] {w!r} too short/failed — retrying with '.'")
                clip = await self._synth_one(fish, w + ".")
            if clip is None or len(clip) < 500:
                print(f"[AckBridge] {w!r}: synth failed/too short — skipping "
                      "(other acks still play)")
                continue
            self._clips[w] = clip
            try:
                np.save(self._cache_path(w), clip)
            except Exception as e:
                print(f"[AckBridge] cache write failed for {w!r}: {e}")
            print(f"[AckBridge] cached {w!r} ({len(clip)/48:.0f}ms)")
        # PARTIAL READY: at least one clip => acks can play (pick_for falls
        # back within a category). A single bad word never silences all acks.
        self._ready = len(self._clips) > 0
        print(f"[AckBridge] ready with {len(self._clips)}/{len(words)} clips "
              f"({'PARTIAL' if len(self._clips) < len(words) else 'full'})")

    async def _synth_one(self, fish, text: str) -> np.ndarray | None:
        """Synthesize one short clip via the Fish provider (48kHz int16 mono)."""
        try:
            async def _texts():
                yield text
            frames = []
            async for audio in fish.synthesize_stream(_texts()):
                frames.append(np.frombuffer(
                    audio.frame.data, dtype=np.int16).copy())
            if not frames:
                return None
            return np.concatenate(frames)
        except Exception as e:
            print(f"[AckBridge] synth {text!r} failed: {type(e).__name__}: {e}")
            return None

    def pick_for(self, text: str, turn_relation: str, turn_no: int,
                 ) -> tuple[np.ndarray | None, str | None, str | None]:
        """Semantic ack selection for a turn.

        Returns (clip|None, word|None, reason|None). Caller skips when
        clip is None (silence is the right move).

        Robustness (owner 2026-08-30, 'filler words did not speak out'): if
        the chosen word's clip is missing (Fish refused that word), fall
        back to the first available sibling in the SAME category — semantic
        meaning preserved, never a wrong-category word.
        """
        word, reason = pick_ack_for(text, turn_relation, turn_no)
        if word is None:
            return None, None, reason
        clip = self._clips.get(word)
        if clip is not None:
            return clip, word, reason
        # category fallback
        cat = reason  # pick_ack_for returns the category as reason
        for w in ACK_POOL.get(cat, []):
            c = self._clips.get(w)
            if c is not None:
                return c, w, f"sibling_fallback:{cat}"
        return None, None, f"missing_clip:{reason}"
