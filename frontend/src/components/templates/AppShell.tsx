import { useEffect } from "react";
import { AvatarCanvas } from "../../features/avatar/AvatarCanvas";
import { stateOrder, tanishiStates } from "../../features/avatar/states";
import { ChatPanel } from "../../features/chat/ChatPanel";
import { HudOverlay } from "../../features/hud/HudOverlay";
import { useAppStore } from "../../features/state/store";
import { WidgetPanel } from "../../features/widget/WidgetPanel";

export function AppShell() {
  const ui = useAppStore((s) => s.ui);
  const avatar = useAppStore((s) => s.avatar);
  const sync = useAppStore((s) => s.sync);
  const setMode = useAppStore((s) => s.setMode);
  const setEmotionState = useAppStore((s) => s.setEmotionState);
  const hydrate = useAppStore((s) => s.hydrateRuntimeData);

  useEffect(() => {
    void hydrate();
    const interval = window.setInterval(() => void hydrate(), 30000);
    return () => window.clearInterval(interval);
  }, [hydrate]);

  const apiErrorBanner =
    sync.connectionState === "error" ? (
      <div className="api-error-banner" role="alert">
        <span>Cannot reach Tanishi API (status, chat, notifications). </span>
        <span className="api-error-hint">
          Open the app from the server URL (e.g. http://127.0.0.1:8888/) or run <code>npm run dev</code> with Tanishi on
          8888 so requests proxy through Vite.
        </span>
        <button type="button" className="api-error-retry" onClick={() => void hydrate()}>
          Retry
        </button>
      </div>
    ) : null;

  if (ui.mode === "widget") {
    return (
      <>
        {apiErrorBanner}
        <WidgetPanel />
      </>
    );
  }

  if (ui.mode === "takeover") {
    return (
      <>
        {apiErrorBanner}
        <section className="takeover">
          <AvatarCanvas state={avatar.emotionState} speakingLevel={avatar.speakingLevel} />
          <div className="tk-text">
            <div className="tk-kicker">{tanishiStates[avatar.emotionState].kicker}</div>
            <div className="tk-line">"{tanishiStates[avatar.emotionState].caption}"</div>
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      {apiErrorBanner}
      <div className="toolbar">
        <button className={(ui.mode as string) === "stage" ? "active" : ""} onClick={() => setMode("stage")}>Stage</button>
        <button className={(ui.mode as string) === "widget" ? "active" : ""} onClick={() => setMode("widget")}>Desktop</button>
        <button className={(ui.mode as string) === "takeover" ? "active" : ""} onClick={() => setMode("takeover")}>Fullscreen</button>
      </div>
      <main className="stage">
        <aside className="rail-l">
          <div className="brand">
            <div className="brand-mark">Tan<em>ishi</em></div>
            <div className="brand-v">v0.4 hybrid</div>
          </div>
          <div className="rail-section">
            <div className="rail-h"><span>Emotional States</span><span className="dot" /></div>
            {stateOrder.map((stateKey) => (
              <button
                key={stateKey}
                className={`state-btn ${avatar.emotionState === stateKey ? "active" : ""}`}
                onClick={() => setEmotionState(stateKey)}
              >
                <span>{tanishiStates[stateKey].label}</span>
                <span className="glyph">{tanishiStates[stateKey].code}</span>
              </button>
            ))}
          </div>
        </aside>
        <section className="center">
          <HudOverlay />
          <AvatarCanvas state={avatar.emotionState} speakingLevel={avatar.speakingLevel} />
          <div className="avatar-caption">
            <div className="state-label">{tanishiStates[avatar.emotionState].kicker}</div>
            <div className="line">{tanishiStates[avatar.emotionState].caption}</div>
          </div>
        </section>
        <ChatPanel />
      </main>
    </>
  );
}
