/**
 * Speech-to-text via browser Web Speech API (Chrome/Edge/Safari 14.1+).
 * Not the same as terminal faster-whisper — uses cloud recognition where the browser sends audio.
 */

/** TS `lib.dom` omits the ctor in some versions; keep a minimal shape we actually use. */
interface BrowserSpeechAlternative {
  readonly transcript: string;
}

interface BrowserSpeechResult {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: BrowserSpeechAlternative;
}

interface BrowserSpeechResultList {
  readonly length: number;
  readonly [index: number]: BrowserSpeechResult;
}

interface BrowserSpeechRecognitionEvent extends Event {
  readonly resultIndex: number;
  readonly results: BrowserSpeechResultList;
}

interface BrowserSpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}

interface BrowserSpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((this: BrowserSpeechRecognition, ev: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((this: BrowserSpeechRecognition, ev: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

type RecognitionCtor = new () => BrowserSpeechRecognition;

function getRecognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function isSpeechRecognitionSupported(): boolean {
  return getRecognitionCtor() !== null;
}

export type DictationHandlers = {
  /** Best-effort live transcript while speaking */
  onInterim?: (text: string) => void;
  /** Final phrase when the browser closes the utterance */
  onFinal: (text: string) => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
};

/**
 * Start one utterance (push-to-talk style). Call `stop()` to cancel early.
 * Requires HTTPS or localhost; user must allow microphone when prompted.
 */
export function startDictation(handlers: DictationHandlers & { lang?: string }): { stop: () => void } {
  const Ctor = getRecognitionCtor();
  if (!Ctor) {
    handlers.onError?.("Speech recognition is not supported in this browser.");
    handlers.onEnd?.();
    return { stop: () => {} };
  }

  const rec = new Ctor();
  rec.lang = handlers.lang ?? "en-US";
  rec.continuous = false;
  rec.interimResults = true;
  rec.maxAlternatives = 1;

  rec.onresult = (event: BrowserSpeechRecognitionEvent) => {
    let interim = "";
    let finalChunk = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const row = event.results[i];
      const piece = row[0]?.transcript ?? "";
      if (row.isFinal) finalChunk += piece;
      else interim += piece;
    }
    const trimmedInterim = interim.trim();
    if (trimmedInterim) handlers.onInterim?.(trimmedInterim);
    const trimmedFinal = finalChunk.trim();
    if (trimmedFinal) handlers.onFinal(trimmedFinal);
  };

  rec.onerror = (event: BrowserSpeechRecognitionErrorEvent) => {
    if (event.error === "aborted") return;
    let msg = event.message || event.error;
    if (event.error === "not-allowed") {
      msg = "Microphone blocked. Allow mic access for this site in browser settings.";
    } else if (event.error === "no-speech") {
      msg = "No speech heard — try again a bit closer to the mic.";
    }
    handlers.onError?.(msg);
  };

  rec.onend = () => {
    handlers.onEnd?.();
  };

  try {
    rec.start();
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : String(err));
    handlers.onEnd?.();
    return { stop: () => {} };
  }

  return {
    stop: () => {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    },
  };
}
