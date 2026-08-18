import numpy as np
from enum import Enum
import ten_vad

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
    def __init__(self, hop_size=256, threshold=0.5, silence_duration_ms=500, sample_rate=16000):
        self.vad = ten_vad.TenVad(hop_size=hop_size, threshold=threshold)
        self.hop_size = hop_size
        self.threshold = threshold
        self.buffer = np.array([], dtype=np.int16)
        
        self.is_speaking = False
        self.silence_frames_threshold = int((silence_duration_ms / 1000) * sample_rate / hop_size)
        self.speech_frames_threshold = 2
        
        self.silence_frames = 0
        self.speech_frames = 0

    def process_audio(self, audio_chunk: np.ndarray) -> list[VADEvent]:
        self.buffer = np.concatenate((self.buffer, audio_chunk.astype(np.int16)))
        events = []
        
        while len(self.buffer) >= self.hop_size:
            frame = self.buffer[:self.hop_size]
            self.buffer = self.buffer[self.hop_size:]
            
            # process frame -> returns (probability, classification)
            prob, is_speech_class = self.vad.process(frame)
            is_speech = prob > self.threshold
            
            if is_speech:
                self.speech_frames += 1
                self.silence_frames = 0
                if not self.is_speaking and self.speech_frames >= self.speech_frames_threshold:
                    self.is_speaking = True
                    events.append(VADEvent.SPEECH_STARTED)
            else:
                self.silence_frames += 1
                self.speech_frames = 0
                if self.is_speaking and self.silence_frames >= self.silence_frames_threshold:
                    self.is_speaking = False
                    events.append(VADEvent.SPEECH_ENDED)
                    
        return events

def get_vad_provider() -> VADProvider:
    return TenVADProvider()
