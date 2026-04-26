import { clsx } from "clsx";
import { memo } from "react";
import { useAppStore } from "../state/store";

export const HudOverlay = memo(function HudOverlay() {
  const hud = useAppStore((s) => s.hud);
  const avatarState = useAppStore((s) => s.avatar.emotionState);
  const sync = useAppStore((s) => s.sync);
  const m = hud.metrics;

  if (!hud.enabled) return null;

  return (
    <div
      className={clsx("hud-overlay", {
        "hud-overlay--alert": hud.alertLevel === "high",
        "hud-overlay--warn": hud.alertLevel === "medium",
      })}
      aria-hidden
    >
      <div className="hud hud-tl">
        <div>TANISHI CORE</div>
        <div className="val">
          STATE <b>{avatarState}</b>
        </div>
        <div className="val">
          SYNC <b>{sync.connectionState}</b>
        </div>
      </div>
      <div className="hud hud-tr">
        <div>{new Date().toLocaleTimeString()}</div>
        <div className="val">
          ROUTE <b>{m.route ?? "—"}</b>
        </div>
        <div className="val">
          TOOLS <b>{m.tools ?? "—"}</b>
        </div>
      </div>
      <div className="hud hud-bl">
        <div>HYBRID</div>
        <div className="val">
          {m.cloud ?? "—"}
        </div>
        <div className="val">
          {m.local ?? "—"}
        </div>
      </div>
      <div className="hud hud-br">
        <div>AUTONOMY</div>
        <div className="val">
          TASKS <b>{m.tasks ?? "—"}</b>
        </div>
        <div className="val">
          INBOX <b>{m.notify ?? "—"}</b>
        </div>
      </div>
    </div>
  );
});
