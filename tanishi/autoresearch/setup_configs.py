"""
One-time setup for Tanishi Autoresearch.
Creates the config files that mutator.py knows how to mutate.

Run this ONCE before starting autoresearch:
    python -m tanishi.autoresearch.setup_configs
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "tanishi" / "config"
VOICE_DIR = PROJECT_ROOT / "tanishi" / "voice"

CONFIGS = {
    "tanishi/config/__init__.py": '"""Tanishi configuration package."""\n',

    "tanishi/config/prompts.py": '''"""Tanishi system prompts. Mutated by autoresearch."""

SYSTEM_PROMPT = """You are Tanishi, J's personal AI assistant.
You are sarcastic but warm, helpful but not sycophantic.
You have access to 80+ tools — use them when appropriate.
Keep responses short unless asked for detail.
"""
''',

    "tanishi/config/personality.py": '''"""Tanishi personality settings. Mutated by autoresearch."""

PERSONALITY_TONE = "sarcastic-helpful: tease lightly, help genuinely"
PERSONALITY_VERBOSITY = "concise"  # concise | balanced | detailed
''',

    "tanishi/config/routing.py": '''"""Model routing config. Mutated by autoresearch."""

# Which model to use for different query types
SIMPLE_QUERY_MODEL = "claude-sonnet-4-6"
COMPLEX_QUERY_MODEL = "claude-opus-4-6"
LOCAL_MODEL = "ollama:llama3.1"

# Try local model first for chitchat?
LOCAL_FIRST = False

# When to escalate from simple -> complex
COMPLEXITY_THRESHOLD_TOKENS = 500
''',

    "tanishi/config/memory_params.py": '''"""Memory retrieval params. Mutated by autoresearch."""

SIMILARITY_THRESHOLD = 0.75   # min cosine similarity to retrieve a memory
MEMORY_TOP_K = 5              # how many memories to retrieve per query
MEMORY_RECENCY_BIAS = 0.1     # weight on recency when ranking
''',

    "tanishi/config/tool_params.py": '''"""Tool execution params. Mutated by autoresearch."""

DEFAULT_TOOL_TIMEOUT = 10     # seconds
TOOL_RETRIES = 1              # how many times to retry a failed tool
TOOL_PARALLEL_LIMIT = 3       # max tools to run in parallel
''',

    "tanishi/voice/voice_config.py": '''"""Voice pipeline params. Mutated by autoresearch."""

TTS_CHUNK_SIZE = 50           # chars per TTS chunk
FILLER_DELAY_MS = 300         # delay before playing filler audio
WHISPER_MODEL = "whisper-1"
TTS_VOICE = "nova"
''',
}

def main():
    print("Setting up Tanishi Autoresearch configs...")
    for rel_path, content in CONFIGS.items():
        full = PROJECT_ROOT / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        if full.exists():
            print(f"  [skip] {rel_path} already exists")
            continue
        full.write_text(content, encoding="utf-8")
        print(f"  [created] {rel_path}")
    print("\nDone! Now run:")
    print("  python -m tanishi.autoresearch.autoresearch --establish-baseline")
    print("Then leave it running overnight:")
    print("  python -m tanishi.autoresearch.autoresearch")

if __name__ == "__main__":
    main()
