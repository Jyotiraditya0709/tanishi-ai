import { wsClientV2HandshakeSchema } from "./contracts";
import { getWsUrl } from "./apiBase";
import { parseWsInbound } from "./wsParse";

type CanvasFrame = { kind: "mermaid" | "chart" | "html"; payload: string };
export type WsThinkResult = { text: string; canvases: CanvasFrame[] };

/**
 * Stream a completion over `/ws` using protocol v2 (JSON envelopes).
 * Falls back is not used here — plain-legacy clients should keep using REST `/chat`.
 */
export function streamThinkOverWs(userMessage: string): Promise<WsThinkResult> {
  const handshake = wsClientV2HandshakeSchema.parse({ message: userMessage });
  const payload = JSON.stringify(handshake);

  return new Promise((resolve, reject) => {
    const ws = new WebSocket(getWsUrl());
    let assembled = "";
    const canvases: CanvasFrame[] = [];
    let settled = false;

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      fn();
    };

    ws.onclose = () => {
      if (settled) return;
      if (assembled.length > 0) {
        finish(() => resolve({ text: assembled, canvases }));
      } else {
        finish(() => reject(new Error("WebSocket closed before completion")));
      }
    };

    ws.onerror = () => {
      finish(() => reject(new Error("WebSocket connection error")));
    };

    ws.onmessage = (event) => {
      const parsed = parseWsInbound(String(event.data));
      if (parsed.kind === "invalid") {
        finish(() => reject(new Error("Invalid WebSocket frame")));
        return;
      }
      const { frame } = parsed;
      switch (frame.type) {
        case "chunk":
          assembled += frame.text;
          break;
        case "canvas":
          canvases.push({ kind: frame.kind, payload: frame.payload });
          break;
        case "end":
          finish(() => resolve({ text: assembled, canvases }));
          break;
        case "error":
          finish(() => reject(new Error(frame.detail || "Unknown error")));
          break;
        default:
          break;
      }
    };

    ws.onopen = () => {
      ws.send(payload);
    };
  });
}
