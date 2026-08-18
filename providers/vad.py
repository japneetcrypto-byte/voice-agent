# Interface for Voice Activity Detection (TEN VAD)

class VADProvider:
    def process_audio(self, audio_chunk):
        raise NotImplementedError
