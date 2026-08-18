# Interface for LLM (Gemini Flash)

class LLMProvider:
    def generate_response(self, messages):
        raise NotImplementedError
