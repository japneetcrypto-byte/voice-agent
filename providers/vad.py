import os
import time
import numpy as np
from enum import Enum
import ten_vad
from providers.endpointing import HangoverTracker, GRADUAL

class VADEvent(Enum):
    SPEECH_STARTED = "SPEECH_STARTED"
    SPEECH_ENDED = "SPEECH_ENDED"

class VADProvider:
    def process_audio(self, audio_chunk: np.ndarray) -> list[VADEvent]:
        """
        Process incoming audio chunk (int16 PCM) and yield VADEvents.
        """
        raise NotImplementedError

class TenVADProvider(VADProvider):
    """Adaptive endpointing (owner brief 2026-08-27) — no architecture change.

    Same SPEECH_STARTED/SPEECH_ENDED events; the end-of-turn DECISION becomes
    adaptive so normal mid-story pauses don't trigger the agent:

    - base silence threshold unchanged (AIVA_SILENCE_MS, default 300 ms) —
      short turns stay fast
    - PREMATURE-RESUME PENALTY: if speech resumes within RESUME_WINDOW_MS after
      an endpoint, the next endpoint requires +PENALTY_STEP_MS more trailing
      silence (capped at AIVA_MAX_SILENCE_MS, default 1100 ms)
    - LONG-SPEECH FLOOR: after LONG_SPEECH_AFTER_MS of accumulated speech in
      the current stretch, require at least LONG_SPEECH_FLOOR_MS of trailing
      silence (continuous speakers pause mid-story without finishing)
    - GENUINE-GAP RESET: GENUINE_GAP_MS of real silence clears the penalty
      (a real turn change happened)

    Full endpoint evidence is exposed via self.last_endpoint and
    self.last_resume_gap_ms for instrumentation. Deterministic throughout.
    """

    # Calibrated against the independent gold-vs-log evaluation (2026-08-27,
    # continuous-speaker run: >=8/32 premature endpoints, ~24 gold turns
    # fragmented into 32 STT events). Root mismatch: Hindi planning pauses run
    # 1.5-3s — the old 1.5s resume window missed them, so every thinking pause
    # re-endpointed at base speed.
    RESUME_WINDOW_MS = 3000      # pauses up to 3s still count as continuation
    GENUINE_GAP_MS = 4000        # >4s silence = real turn change (reset speed)
    LONG_SPEECH_FLOOR_MS = 700   # unchanged
    LONG_SPEECH_AFTER_MS = 5000  # treat as continuous speaker earlier (was 8s)
    PENALTY_STEP_MS = 400        # reach max wait after 2 premature cycles

    def __init__(self, hop_size=256, threshold=0.5, silence_duration_ms=None,
                 sample_rate=16000, min_speech_ms=200, max_silence_ms=None):
        self.vad = ten_vad.TenVad(hop_size=hop_size, threshold=threshold)
        self.hop_size = hop_size
        self.threshold = threshold
        self.buffer = np.array([], dtype=np.int16)
        self.sample_rate = sample_rate
        self.hop_ms = hop_size / sample_rate * 1000.0

        self.base_silence_ms = int(silence_duration_ms if silence_duration_ms is not None
                                    else os.getenv("AIVA_SILENCE_MS", "300"))
        self.max_silence_ms = int(max_silence_ms if max_silence_ms is not None
                                   else os.getenv("AIVA_MAX_SILENCE_MS", "1100"))
        self.silence_frames_threshold = int((self.base_silence_ms / 1000) * sample_rate / hop_size)
        self.speech_frames_threshold = int((min_speech_ms / 1000) * sample_rate / hop_size)

        self.is_speaking = False
        self.silence_frames = 0
        self.speech_frames = 0

        # adaptive state
        self.endpoint_penalty_ms = 0
        self.stretch_speech_ms = 0.0
        self.pending_stretch_speech_ms = 0.0
        self.last_endpoint_monotonic = None
        self.last_endpoint = None            # evidence dict, refreshed per endpoint
        self.last_resume_gap_ms = None       # consumed by main.py instrumentation

        # Hangover (directive 2026-08-29 fix 1): hard energy cuts extend the
        # silence window once; natural decays end at the normal window.
        self.hangover = HangoverTracker(
            hangover_ms=int(os.getenv("AIVA_HANGOVER_MS", "250")))

    # ---- adaptive threshold state machine (deterministic) ----
    def _effective_silence_ms(self) -> float:
        eff = self.base_silence_ms + self.endpoint_penalty_ms
        if self.stretch_speech_ms >= self.LONG_SPEECH_AFTER_MS:
            eff = max(eff, self.LONG_SPEECH_FLOOR_MS)
        return min(eff, self.max_silence_ms)

    def note_premature_resume(self) -> None:
        self.endpoint_penalty_ms = min(
            self.endpoint_penalty_ms + self.PENALTY_STEP_MS,
            max(0, self.max_silence_ms - self.base_silence_ms),
        )

    def _frames_for(self, ms: float) -> int:
        return int(ms / 1000 * self.sample_rate / self.hop_size)

    def process_audio(self, audio_chunk: np.ndarray) -> list[VADEvent]:
        if audio_chunk.dtype == np.float32 or audio_chunk.dtype == np.float64:
            int16_chunk = np.clip(audio_chunk * 32767.0, -32768, 32767).astype(np.int16)
        else:
            int16_chunk = audio_chunk.astype(np.int16)

        self.buffer = np.concatenate((self.buffer, int16_chunk))
        events = []

        while len(self.buffer) >= self.hop_size:
            frame = self.buffer[:self.hop_size]
            self.buffer = self.buffer[self.hop_size:]

            prob, is_speech_class = self.vad.process(frame)
            is_speech = prob > self.threshold

            if is_speech:
                self.speech_frames += 1
                self.silence_frames = 0
                self.stretch_speech_ms += self.hop_ms
                if self.is_speaking:
                    self.hangover.note_speech_frame(int(np.max(np.abs(frame))))
                if not self.is_speaking and self.speech_frames >= self.speech_frames_threshold:
                    self.is_speaking = True
                    self.hangover.reset()
                    # premature-resume detection: endpoint recently declared?
                    if self.last_endpoint_monotonic is not None:
                        gap_ms = (time.monotonic() - self.last_endpoint_monotonic) * 1000.0
                        if gap_ms <= self.RESUME_WINDOW_MS:
                            self.note_premature_resume()
                            self.last_resume_gap_ms = round(gap_ms, 1)
                            # merge the interrupted stretch back (long-speech floor continues)
                            self.stretch_speech_ms += self.pending_stretch_speech_ms
                    self.pending_stretch_speech_ms = 0.0
                    events.append(VADEvent.SPEECH_STARTED)
            else:
                self.silence_frames += 1
                self.speech_frames = 0
                # genuine-gap reset while not speaking
                if (not self.is_speaking
                        and self.silence_frames * self.hop_ms >= self.GENUINE_GAP_MS
                        and self.endpoint_penalty_ms):
                    self.endpoint_penalty_ms = 0
                    self.stretch_speech_ms = 0.0
                if self.is_speaking:
                    # Classify the transition once, at the first silence frame:
                    # was speech energy still near its peak (hard cut) or
                    # already decayed (natural)? Hard cut extends this
                    # endpoint's window by the hangover.
                    if self.silence_frames == 1:
                        profile, hangover_ms = self.hangover.evaluate()
                        self._hangover_extra_frames = (
                            self._frames_for(hangover_ms) if profile == "hard_cut" else 0)
                        self._energy_profile = profile
                    eff = self._effective_silence_ms() + getattr(
                        self, "_hangover_extra_frames", 0) * self.hop_ms
                    if self.silence_frames >= self._frames_for(eff):
                        self.is_speaking = False
                        self.last_endpoint = {
                            "speech_duration_ms": round(self.stretch_speech_ms, 1),
                            "trailing_silence_ms": round(self.silence_frames * self.hop_ms, 1),
                            "threshold_ms": round(eff, 1),
                            "penalty_ms": self.endpoint_penalty_ms,
                            "energy_profile": getattr(self, "_energy_profile", GRADUAL),
                            "hangover_ms": round(getattr(self, "_hangover_extra_frames", 0) * self.hop_ms, 1),
                        }
                        self.last_endpoint_monotonic = time.monotonic()
                        self.pending_stretch_speech_ms = self.stretch_speech_ms
                        self.stretch_speech_ms = 0.0
                        events.append(VADEvent.SPEECH_ENDED)

        return events

def get_vad_provider() -> VADProvider:
    return TenVADProvider(
        silence_duration_ms=int(os.getenv("AIVA_SILENCE_MS", "300")),
        max_silence_ms=int(os.getenv("AIVA_MAX_SILENCE_MS", "1100")),
    )
