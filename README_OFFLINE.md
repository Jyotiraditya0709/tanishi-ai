# Tanishi Offline Mode

## Start Offline
- CLI flag: `python -m tanishi.api.server --offline`
- Env var: `TANISHI_OFFLINE=1 python -m tanishi.api.server`
- Offline mode forces:
  - `default_llm = ollama`
  - `privacy_mode = true`
  - cloud calls disabled

## What Works Offline
- Chat and reasoning through local Ollama models
- Memory retrieval with local embedding search (or keyword fallback)
- Skills and local tools
- Local TTS path (`pyttsx3`) when available

## What Does Not Work Offline
- Live web search
- Cloud TTS providers
- Any network-only integrations
- Web search cache caveat: recent cached search hits can still be returned

## Requirements
- Ollama running locally
- At least one model pulled (for example `gemma3:4b`)

## Testing Offline Mode Without Ollama
1. Stop Ollama intentionally.
2. Run:
   - `TANISHI_OFFLINE=1 python -m tanishi.api.server --offline`
3. Send a chat request (or open dashboard and chat once).
4. Confirm you get:
   - `"Offline mode is enabled but Ollama is not running. Start Ollama first."`
