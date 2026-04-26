import type { StatusResponse } from "./contracts";

export type HudAlertLevel = "low" | "medium" | "high";

export type HudDerived = {
  metrics: Record<string, string>;
  alertLevel: HudAlertLevel;
};

function str(v: unknown, fallback = "—"): string {
  if (v === null || v === undefined) return fallback;
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  return fallback;
}

/** Derive HUD readouts from `/status` brain + autonomy payloads (no fake CPU/RAM). */
export function buildHudFromStatus(status: StatusResponse | null): HudDerived {
  if (!status) {
    return {
      metrics: {
        cloud: "Claude —",
        local: "Ollama —",
        route: "—",
        tasks: "—",
        notify: "—",
      },
      alertLevel: "low",
    };
  }

  const b = status.brain;
  const a = status.autonomy;
  const claude = str(b.claude);
  const ollama = str(b.ollama);
  const tools = str(b.tools, "0");
  const route = str(b.default_llm, "auto");
  const enabled = str(a.enabled_tasks, "0");
  const total = str(a.total_tasks, "0");
  const unread = str(a.unread_notifications, "0");

  const claudeOk = claude === "online";
  const ollamaOk = ollama === "online";
  let alertLevel: HudAlertLevel = "low";
  if (!claudeOk && !ollamaOk) alertLevel = "high";
  else if (!claudeOk || !ollamaOk) alertLevel = "medium";

  return {
    metrics: {
      cloud: `Claude ${claude}`,
      local: `Ollama ${ollama}`,
      route,
      tasks: `${enabled}/${total} on`,
      notify: `${unread} unread`,
      tools,
    },
    alertLevel,
  };
}
