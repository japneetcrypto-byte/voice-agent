# Interface for Speech-to-Text (faster-whisper)

class STTProvider:
    def transcribe(self, audio):
        raise NotImplementedError
