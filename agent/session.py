class Message:
    def __init__(self, role: str, content: str):
        self.role = role  # "user" or "assistant"
        self.content = content

    def to_dict(self):
        return {"role": self.role, "content": self.content}

class ConversationSession:
    def __init__(self):
        # Ephemeral memory — resets completely on every new session instance.
        self.history: list[Message] = []
        self.system_prompt = (
            "You are a helpful, concise conversational voice assistant. "
            "Speak naturally and conversationally. Keep responses relatively "
            "short because your responses will be spoken aloud. Do not produce "
            "markdown unless explicitly requested. Ask clarifying questions when necessary."
        )

    def add_user_message(self, text: str):
        """Append transcribed user speech to the ephemeral history."""
        self.history.append(Message(role="user", content=text))

    def add_agent_message(self, text: str):
        """Append the generated agent response to the ephemeral history."""
        self.history.append(Message(role="assistant", content=text))

    def get_context(self) -> list[dict]:
        """
        Returns the conversation history in a generic dict format.
        The LLM provider will map this generic state to its specific API schema.
        """
        return [msg.to_dict() for msg in self.history]

    def clear(self):
        """Reset the conversation state entirely."""
        self.history.clear()
