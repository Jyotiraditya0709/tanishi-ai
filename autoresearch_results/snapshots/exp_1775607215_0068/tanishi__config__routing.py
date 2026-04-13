"""Model routing config. Mutated by autoresearch."""

# Which model to use for different query types
SIMPLE_QUERY_MODEL = "claude-haiku-4-5-20251001"
COMPLEX_QUERY_MODEL = "claude-opus-4-6"
LOCAL_MODEL = "ollama:llama3.1"

# Try local model first for chitchat?
LOCAL_FIRST = True

# When to escalate from simple -> complex
COMPLEXITY_THRESHOLD_TOKENS = 500
