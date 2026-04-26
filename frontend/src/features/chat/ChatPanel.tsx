import { useEffect, useMemo, useRef, useState } from "react";
import { isSpeechRecognitionSupported, startDictation } from "../../lib/voice/browserListen";
import { getSpeechEnabled, setSpeechEnabled } from "../../lib/voice/browserSpeak";
import { useAppStore } from "../state/store";

export function ChatPanel() {
  const chat = useAppStore((s) => s.chat);
  const submitChat = useAppStore((s) => s.submitChat);
  const [input, setInput] = useState("");
  const [voiceOn, setVoiceOn] = useState(getSpeechEnabled);
  const [listening, setListening] = useState(false);
  const [hearingPreview, setHearingPreview] = useState("");
  const [dictationError, setDictationError] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const dictationStopRef = useRef<(() => void) | null>(null);
  const canListen = useMemo(() => isSpeechRecognitionSupported(), []);

  const waveformBars = useMemo(
    () =>
      Array.from({ length: 40 }).map((_, idx) => ({
        key: idx,
        height:
          4 +
          Math.abs(Math.sin(idx * 0.35) * Math.cos(idx * 0.22)) *
            (chat.streamingState === "speaking" ? 20 : 6),
      })),
    [chat.streamingState],
  );

  const send = async () => {
    if (!input.trim() || chat.isThinking) return;
    const payload = input;
    setInput("");
    await submitChat(payload);
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  };

  useEffect(() => {
    return () => {
      dictationStopRef.current?.();
      dictationStopRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (chat.isThinking && listening) {
      dictationStopRef.current?.();
      dictationStopRef.current = null;
      setListening(false);
      setHearingPreview("");
    }
  }, [chat.isThinking, listening]);

  const toggleMic = () => {
    if (!canListen || chat.isThinking) return;
    if (listening) {
      dictationStopRef.current?.();
      dictationStopRef.current = null;
      setListening(false);
      setHearingPreview("");
      return;
    }
    setDictationError("");
    setHearingPreview("");
    const { stop } = startDictation({
      lang: typeof navigator !== "undefined" ? navigator.language : "en-US",
      onInterim: (text) => setHearingPreview(text),
      onFinal: (text) => {
        setInput((prev) => {
          const p = prev.trimEnd();
          return p ? `${p} ${text}` : text;
        });
      },
      onError: (msg) => {
        setDictationError(msg);
        setHearingPreview("");
      },
      onEnd: () => {
        dictationStopRef.current = null;
        setListening(false);
        setHearingPreview("");
      },
    });
    dictationStopRef.current = stop;
    setListening(true);
  };

  return (
    <aside className="rail-r">
      <header className="r-head">
        <div className="title">Transcript</div>
        <div className="r-head-actions">
          <div className="status">Live</div>
          {typeof window !== "undefined" && "speechSynthesis" in window ? (
            <label className="voice-toggle" htmlFor="tanishi-voice-toggle">
              <input
                id="tanishi-voice-toggle"
                type="checkbox"
                checked={voiceOn}
                onChange={() => {
                  const next = !voiceOn;
                  setVoiceOn(next);
                  setSpeechEnabled(next);
                }}
              />
              <span>Voice</span>
            </label>
          ) : null}
        </div>
      </header>
      <div className="transcript" ref={scroller}>
        {chat.messages.map((message) => (
          <div key={message.id} className={`msg ${message.role === "assistant" ? "t" : "u"}`}>
            <div className="who">{message.role === "assistant" ? "Tanishi" : "Boss"}</div>
            <div className="bub">{message.pending ? <span className="ghost" /> : message.text}</div>
          </div>
        ))}
      </div>
      <div className="wave">
        {waveformBars.map((bar) => (
          <span key={bar.key} className="bar" style={{ height: `${bar.height}px` }} />
        ))}
      </div>
      <div className="composer-block">
        <div className="composer">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Say something to her..."
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void send();
              }
            }}
            disabled={chat.isThinking}
            aria-label="Message"
          />
          {canListen ? (
            <button
              type="button"
              className={`composer-mic${listening ? " listening" : ""}`}
              onClick={() => toggleMic()}
              disabled={chat.isThinking}
              title={listening ? "Stop listening" : "Speak — uses browser mic (Web Speech API)"}
            >
              {listening ? "Stop" : "Mic"}
            </button>
          ) : null}
          <button type="button" className="send" onClick={() => void send()} disabled={chat.isThinking}>
            Send
          </button>
        </div>
        {dictationError ? (
          <div className="composer-hint composer-hint--err" role="status">
            {dictationError}
          </div>
        ) : listening ? (
          <div className="composer-hint" role="status" aria-live="polite">
            {hearingPreview ? `Hearing: ${hearingPreview}` : "Listening…"}
          </div>
        ) : null}
      </div>
    </aside>
  );
}
