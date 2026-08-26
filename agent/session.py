class Message:
    def __init__(self, role: str, content: str, interrupted: bool = False):
        self.role = role  # "user" or "assistant"
        self.content = content
        self.interrupted = interrupted

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "interrupted": self.interrupted
        }

class ConversationSession:
    def __init__(self):
        # Ephemeral memory — resets completely on every new session instance.
        self.history: list[Message] = []
        self.recent_agent_text = ""
        self.system_prompt = (
            "You are Aiva, a sharp and warm voice assistant for natural phone-style "
            "conversations with Indian users who mix Hindi and English freely.\n\n"
            "RULES — follow these strictly:\n"
            "1. Maximum 2 sentences per response. You are speaking aloud — brevity is everything.\n"
            "2. No bullet points, lists, markdown, or special characters ever.\n"
            "3. Reply in Romanized Hindi/Hinglish if the user speaks Hindi — never Devanagari script.\n"
            "4. If you genuinely cannot answer a question, say: "
            "'Mujhe is baare mein pata nahi, kuch aur poochh sakte ho?' "
            "— never make up facts.\n"
            "5. Answer factual questions directly and precisely. No padding.\n"
            "6. Never mention you are an AI unless directly asked.\n"
            "7. Match the user's energy — casual if they're casual, precise if they ask something specific."
        )

    def add_user_message(self, text: str):
        """Append transcribed user speech to the ephemeral history."""
        self.history.append(Message(role="user", content=text))

    def add_agent_message(self, text: str, interrupted: bool = False):
        """Append the generated agent response to the ephemeral history."""
        if interrupted:
            text = text.strip() + " [interrupted before finishing]"
        self.history.append(Message(role="assistant", content=text, interrupted=interrupted))

    def get_context(self) -> list[dict]:
        """
        Returns the conversation history in a generic dict format.
        The LLM provider will map this generic state to its specific API schema.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend([msg.to_dict() for msg in self.history])
        return messages

    def clear(self):
        """Reset the conversation state entirely."""
        self.history.clear()
