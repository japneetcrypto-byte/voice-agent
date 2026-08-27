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
        # - no pin: Whisper auto-detect drifts on short Hinglish (es/ro/en outputs)
        # - 'en' pin on Hindi speech: English news-anchor hallucinations + <|hi|>
        #   token leaks (2026-08-27 run) — mismatch between pin and actual speech
        # - initial_prompt "Roman Hindi, Hindi and English words": LEAKED into
        #   transcripts on unclear audio ("Raman Hindi, Hindi and English words.")
        #   -> prompt removed by default (evidence-based), env-overridable.
        # Language config: 'hi' pin handles Hindi + Hinglish + English words
        # (default); pure-English rooms set AIVA_STT_LANGUAGE=en. temperature=0
        # suppresses repetition loops.
        stt_language = os.getenv("AIVA_STT_LANGUAGE", "hi")
        stt_temperature = float(os.getenv("AIVA_STT_TEMPERATURE", "0.0"))
        stt_prompt = os.getenv("AIVA_STT_PROMPT", "")  # default: no prompt (leak evidence)
        kwargs = dict(
            file=("audio.wav", wav_io.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            language=stt_language,
            temperature=stt_temperature,
        )
        if stt_prompt:
            kwargs["prompt"] = stt_prompt
        transcription = self.client.audio.transcriptions.create(**kwargs)
        
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
