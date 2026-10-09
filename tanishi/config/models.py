"""Single source of truth for all model identifiers. Change a model here, not in feature modules."""

# Claude — values used by the live routing path (routing.py)
CLAUDE_SIMPLE = "claude-haiku-4-5-20251001"
CLAUDE_COMPLEX = "claude-opus-4-6"

# Claude — config.claude_model default + most feature modules
CLAUDE_DEFAULT = "claude-sonnet-4-20250514"

# Local
OLLAMA_DEFAULT = "gemma3:4b"

# OpenAI / voice
OPENAI_STT = "whisper-1"
OPENAI_TTS = "tts-1"
OPENAI_REALTIME = "gpt-realtime"
OPENAI_TTS_VOICE_DEFAULT = "nova"
