"""STT providers: Gemini Live (primary) + Groq Whisper (fallback).

Switch via AIVA_STT_PROVIDER env:
  gemini_live  (default) — Gemini 3.5 Transcribe Live streaming, Groq fallback
  groq         — Groq whisper-large-v3 batch only
"""
import numpy as np
from faster_whisper import WhisperModel

class Transcript:
    def __init__(self, text: str, language: str = "", 
                 no_speech_prob: float = None,
                 avg_logprob: float = None,
                 compression_ratio: float = None):
        self.text = text
        self.language = language
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob
        self.compression_ratio = compression_ratio

class STTProvider:
    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        raise NotImplementedError

import os
import io
import scipy.io.wavfile as wavfile
from groq import Groq

def devanagari_to_roman(text: str) -> str:
    """Module-level so the echo filter can compare in a common script.
    The STT transcript itself is NO LONGER romanized (owner decision 2026-08-27):
    the LLM reads Devanagari natively; only comparisons vs Roman text use this."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    if any('\u0900' <= c <= '\u097F' for c in text):
        return transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
    return text


class GroqSTT(STTProvider):
    def __init__(self):
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        # Product scope: Hindi/English/Hinglish for Indian users.
        # Default pin to 'hi' — handles Hindi, English words, and code-switching.
        # Auto-detect on the first utterance is unreliable (greetings are
        # ambiguous, leading to wrong-language locks like English/Urdu/Filipino).
        # Override only for non-Hindi use cases: AIVA_STT_LANGUAGE=en etc.
        forced = os.getenv("AIVA_STT_LANGUAGE", "hi").strip().lower()
        self.session_language = forced
        self.auto_mode = False

    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        if len(audio_data) < 4000:
            return Transcript(text="", language="auto")
        
        int_audio = (audio_data * 32767).astype(np.int16)
        wav_io = io.BytesIO()
        wavfile.write(wav_io, 16000, int_audio)
        wav_io.seek(0)
        wav_io.name = "audio.wav"

        stt_temperature = float(os.getenv("AIVA_STT_TEMPERATURE", "0.0"))
        stt_prompt = os.getenv("AIVA_STT_PROMPT", "")
        stt_model = os.getenv("AIVA_STT_MODEL", "whisper-large-v3")
        kwargs = dict(
            file=("audio.wav", wav_io.read()),
            model=stt_model,
            response_format="verbose_json",
            temperature=stt_temperature,
        )
        if self.session_language:
            kwargs["language"] = self.session_language
        if stt_prompt:
            kwargs["prompt"] = stt_prompt
        transcription = self.client.audio.transcriptions.create(**kwargs)

        # session language learning
        if self.auto_mode:
            duration_ms = len(audio_data) / 16
            n_words = len((transcription.text or "").split())
            seg_conf = None
            if getattr(transcription, "segments", None):
                seg = transcription.segments[0]
                seg_conf = seg.get("avg_logprob") if isinstance(seg, dict) else getattr(seg, "avg_logprob", None)
            qualifies = (duration_ms >= 1200 and n_words >= 3
                          and (seg_conf is None or seg_conf >= -1.0))
            detected = normalize_lang(getattr(transcription, "language", "") or "")
            if detected and detected not in ("hi", "en"):
                detected = None
            if detected:
                if not self.session_language:
                    if qualifies:
                        self.session_language = detected
                        self.mismatch_streak = 0
                        print(f"[STT] session language learned: {detected}")
                    else:
                        print(f"[STT] detection '{detected}' not qualifying yet — staying unpinned")
                else:
                    if seg_conf is not None and seg_conf < -1.0:
                        self.mismatch_streak = getattr(self, "mismatch_streak", 0) + 1
                        if self.mismatch_streak >= 2:
                            self.session_language = None
                            self.mismatch_streak = 0
                            print("[STT] confidence poor twice — re-opening language detection")
                    else:
                        self.mismatch_streak = 0
        
        cleaned = transcription.text.strip()
        
        no_speech_prob = None
        avg_logprob = None
        compression_ratio = None

        if hasattr(transcription, 'segments') and transcription.segments:
            seg = transcription.segments[0]
            if isinstance(seg, dict):
                no_speech_prob = seg.get('no_speech_prob')
                avg_logprob = seg.get('avg_logprob')
                compression_ratio = seg.get('compression_ratio')
            else:
                no_speech_prob = getattr(seg, 'no_speech_prob', None)
                avg_logprob = getattr(seg, 'avg_logprob', None)
                compression_ratio = getattr(seg, 'compression_ratio', None)

        detected_language = getattr(transcription, 'language', "auto")
        return Transcript(
            text=cleaned, 
            language=detected_language,
            no_speech_prob=no_speech_prob,
            avg_logprob=avg_logprob,
            compression_ratio=compression_ratio
        )

_LANG_MAP = {"hindi": "hi", "english": "en", "urdu": "hi"}

def normalize_lang(name: str) -> str | None:
    if not name:
        return None
    low = name.lower()
    if low in _LANG_MAP:
        return _LANG_MAP[low]
    return low if len(low) == 2 else None


# Default export: Gemini Live primary + Groq fallback, or Groq only
def get_stt_provider() -> STTProvider:
    provider = os.getenv("AIVA_STT_PROVIDER", "gemini_live")
    if provider == "gemini_live":
        try:
            from providers.stt_gemini_live import GeminiLiveSTT
            return GeminiLiveSTT()
        except (ValueError, ImportError) as e:
            print(f"[STT] Gemini Live unavailable ({e}), falling back to Groq")
            return GroqSTT()
    return GroqSTT()
