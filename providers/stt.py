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
        """
        Transcribe a single audio segment.
        audio_data: float32 numpy array of audio samples at 16kHz
        """
        raise NotImplementedError

import os
import io
import scipy.io.wavfile as wavfile
from groq import Groq

class GroqSTT(STTProvider):
    def __init__(self):
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        # Natural language detection (owner request 2026-08-27):
        # - forced pin via AIVA_STT_LANGUAGE (hi/en/...) if set
        # - otherwise: auto-detect on the FIRST real utterance (detection is
        #   reliable on longer audio), then pin the session to that language.
        #   Short utterances reuse the session language -> no per-clip drift.
        forced = os.getenv("AIVA_STT_LANGUAGE", "").strip().lower()
        self.session_language = forced or None  # None = not yet detected
        self.auto_mode = not forced

    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        if len(audio_data) < 4000:  # 0.25s minimum, not 0.5s
            return Transcript(text="", language="auto")
        
        # audio_data is float32 [-1.0, 1.0], 16kHz
        # convert to int16 for WAV
        int_audio = (audio_data * 32767).astype(np.int16)
        
        # write to in-memory WAV file
        wav_io = io.BytesIO()
        wavfile.write(wav_io, 16000, int_audio)
        wav_io.seek(0)
        wav_io.name = "audio.wav"

        # STT config history (owner-visible):
        # - no pin: Whisper auto-detect drifts on SHORT clips (es/ro/en outputs)
        #   -> solution: detect on the first utterance, then pin the session
        # - 'en' pin on Hindi speech: English news-anchor hallucinations + <|hi|>
        #   token leaks (2026-08-27 run)
        # - initial_prompt leaked into transcripts on unclear audio -> default OFF
        # Language: forced via AIVA_STT_LANGUAGE; otherwise session auto-detect.
        stt_temperature = float(os.getenv("AIVA_STT_TEMPERATURE", "0.0"))
        stt_prompt = os.getenv("AIVA_STT_PROMPT", "")  # default: no prompt (leak evidence)
        kwargs = dict(
            file=("audio.wav", wav_io.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            temperature=stt_temperature,
        )
        if self.session_language:
            # session language established (forced or learned) -> pin it
            kwargs["language"] = self.session_language
        # else: no language param -> true auto-detect for the first utterance
        if stt_prompt:
            kwargs["prompt"] = stt_prompt
        transcription = self.client.audio.transcriptions.create(**kwargs)

        # learn the session language from the first decent detection
        if self.auto_mode and getattr(transcription, "language", None):
            detected = normalize_lang(transcription.language)
            if detected and not self.session_language:
                self.session_language = detected
                print(f"[STT] session language detected: {detected}")
        
        # Owner decision 2026-08-27: feed Devanagari to the LLM directly.
        # (Roman-Hinglish remains the REPLY style; echo comparison romanizes separately.)
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

def devanagari_to_roman(text: str) -> str:
    """Module-level so the echo filter can compare in a common script.
    The STT transcript itself is NO LONGER romanized (owner decision 2026-08-27):
    the LLM reads Devanagari natively; only comparisons vs Roman text use this."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    if any('\u0900' <= c <= '\u097F' for c in text):
        return transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
    return text


# Default export to be swapped if needed
def get_stt_provider() -> STTProvider:
    return GroqSTT()


# Language map: normalizes Whisper's verbose language names to API codes
_LANG_MAP = {"hindi": "hi", "english": "en", "urdu": "hi"}


def normalize_lang(name: str) -> str | None:
    if not name:
        return None
    low = name.lower()
    if low in _LANG_MAP:
        return _LANG_MAP[low]
    return low if len(low) == 2 else None
