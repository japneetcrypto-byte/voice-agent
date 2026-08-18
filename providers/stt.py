import numpy as np
from faster_whisper import WhisperModel

class Transcript:
    def __init__(self, text: str, language: str = ""):
        self.text = text
        self.language = language

class STTProvider:
    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        """
        Transcribe a single audio segment.
        audio_data: float32 numpy array of audio samples at 16kHz
        """
        raise NotImplementedError

class FasterWhisperSTT(STTProvider):
    def __init__(self, model_size="small"):
        # The model size (small vs medium) will be locked in based on benchmark results.
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        segments, info = self.model.transcribe(audio_data, beam_size=5)
        text = " ".join([segment.text for segment in segments])
        return Transcript(text=text.strip(), language=info.language)

# Default export to be swapped if needed
def get_stt_provider() -> STTProvider:
    return FasterWhisperSTT(model_size="small")
