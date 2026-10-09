"""Voice pipeline params. Mutated by autoresearch."""

from tanishi.config.models import OPENAI_STT, OPENAI_TTS_VOICE_DEFAULT

TTS_CHUNK_SIZE = 40           # chars per TTS chunk
FILLER_DELAY_MS = 300         # delay before playing filler audio
WHISPER_MODEL = OPENAI_STT
TTS_VOICE = OPENAI_TTS_VOICE_DEFAULT
