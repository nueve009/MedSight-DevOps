"""Local TTS output (prototype only — pyttsx3). Not meant to match the
eventual mobile TTS choice (expo-speech), per handoff Section 4."""
import pyttsx3

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = pyttsx3.init()
    return _engine


def speak(text: str) -> None:
    print(f"\n🔊 {text}")
    try:
        engine = _get_engine()
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"(TTS unavailable in this environment: {e})")
