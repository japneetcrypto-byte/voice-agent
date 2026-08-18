# Interface for Text-to-Speech (Fish Audio)

class TTSProvider:
    def synthesize(self, text, reference_id=None):
        raise NotImplementedError
