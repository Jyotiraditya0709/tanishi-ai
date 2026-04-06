"""
Tanishi Voice — Phase 3: She speaks, she listens.

Fully local voice pipeline:
- STT: faster-whisper (GPU accelerated) with SpeechRecognition fallback
- TTS: edge-tts (free Microsoft voices) with pyttsx3 fallback
- Wake word: "Hey Tanishi" keyword detection
- Pipeline: listen → transcribe → think → speak → repeat

All free. All local (STT). All on your GPU.
"""
