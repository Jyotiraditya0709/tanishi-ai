const STORAGE_KEY = "tanishi-browser-speech";

function safeLocalStorageGet(key: string): string | null {
  try {
    const ls = window.localStorage;
    if (!ls || typeof ls.getItem !== "function") return null;
    return ls.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalStorageSet(key: string, value: string): void {
  try {
    const ls = window.localStorage;
    if (!ls || typeof ls.setItem !== "function") return;
    ls.setItem(key, value);
  } catch {
    /* private mode / broken mock */
  }
}

function stripForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]+`/g, " ")
    .replace(/\*\*?|__|\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 8000);
}

export function getSpeechEnabled(): boolean {
  if (typeof window === "undefined") return false;
  return safeLocalStorageGet(STORAGE_KEY) !== "off";
}

export function setSpeechEnabled(on: boolean): void {
  if (typeof window === "undefined") return;
  safeLocalStorageSet(STORAGE_KEY, on ? "on" : "off");
  if (!on && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

/** Prefer English voice; pick a natural-ish default when available. */
function pickVoice(): SpeechSynthesisVoice | null {
  const list = typeof window !== "undefined" ? window.speechSynthesis?.getVoices() ?? [] : [];
  if (!list.length) return null;
  const prefer =
    list.find((v) => /Samantha|Victoria|Karen|Moira|Fiona|Google UK English Female/i.test(v.name)) ??
    list.find((v) => v.lang.startsWith("en") && /female/i.test(v.name)) ??
    list.find((v) => v.lang.startsWith("en"));
  return prefer ?? list[0] ?? null;
}

/**
 * Read assistant text aloud (browser). No server — terminal still uses Python TTS.
 * Requires a prior user gesture on most browsers; sending a chat counts.
 */
export function speakAssistantReply(text: string): void {
  if (!getSpeechEnabled()) return;
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  const plain = stripForSpeech(text);
  if (!plain) return;

  const run = () => {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(plain);
    u.rate = 1.02;
    u.pitch = 1;
    const v = pickVoice();
    if (v) u.voice = v;
    window.speechSynthesis.speak(u);
  };

  if (window.speechSynthesis.getVoices().length === 0) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.onvoiceschanged = null;
      run();
    };
    window.speechSynthesis.getVoices();
    return;
  }
  run();
}
