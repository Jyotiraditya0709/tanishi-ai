"""Model routing config. Mutated by autoresearch."""

from tanishi.config.models import CLAUDE_COMPLEX, CLAUDE_SIMPLE

# Which model to use for different query types
SIMPLE_QUERY_MODEL = CLAUDE_SIMPLE
COMPLEX_QUERY_MODEL = CLAUDE_COMPLEX

# Try local model first for chitchat?
LOCAL_FIRST = True

# When to escalate from simple -> complex
COMPLEXITY_THRESHOLD_TOKENS = 500
