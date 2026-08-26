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

        transcription = self.client.audio.transcriptions.create(
            file=("audio.wav", wav_io.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json"
        )
        
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        def devanagari_to_roman(text: str) -> str:
            # Check if text contains Devanagari characters
            if any('\u0900' <= c <= '\u097F' for c in text):
                return transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
            return text  # already Roman, return as-is
            
        cleaned = devanagari_to_roman(transcription.text.strip())
        
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

# Default export to be swapped if needed
def get_stt_provider() -> STTProvider:
    return GroqSTT()
