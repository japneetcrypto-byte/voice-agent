from typing import AsyncGenerator
import os
from google import genai
from google.genai import types

class LLMProvider:
    async def generate_response_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """
        Generate a streaming response from the LLM based on conversation context.
        Yields text chunks as they are generated.
        """
        raise NotImplementedError

class GeminiLLM(LLMProvider):
    def __init__(self, model: str = "gemini-3.5-flash-lite"):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model

    async def generate_response_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        system_instruction = None
        contents = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
                continue
            
            # Map generic roles to Gemini specific roles
            gemini_role = "user" if msg["role"] == "user" else "model"
            
            contents.append(
                types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
            )
            
        config_kwargs = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
            
        # Optional: Add temperature, max_output_tokens
        config_kwargs["temperature"] = 0.7
        config = types.GenerateContentConfig(**config_kwargs)
        
        response_stream = await self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config
        )
        
        async for chunk in response_stream:
            if chunk.text:
                yield chunk.text

def get_llm_provider() -> LLMProvider:
    return GeminiLLM()
