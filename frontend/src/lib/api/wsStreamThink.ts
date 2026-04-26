import { wsClientV2HandshakeSchema } from "./contracts";
import { getWsUrl } from "./apiBase";
import { errorDetailFromEnvelope, parseWsInbound, textFromChatTokenEnvelope } from "./wsParse";

/**
 * Stream a completion over `/ws` using protocol v2 (JSON envelopes).
 * Falls back is not used here — plain-legacy clients should keep using REST `/chat`.
 */
export function streamThinkOverWs(userMessage: string): Promise<string> {
  const handshake = wsClientV2HandshakeSchema.parse({ protocol: "v2", message: userMessage });
  const payload = JSON.stringify(handshake);

  return new Promise((resolve, reject) => {
    const ws = new WebSocket(getWsUrl());
    let assembled = "";
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
        finish(() => resolve(assembled));
      } else {
        finish(() => reject(new Error("WebSocket closed before completion")));
      }
    };

    ws.onerror = () => {
      finish(() => reject(new Error("WebSocket connection error")));
    };

    ws.onmessage = (event) => {
      const parsed = parseWsInbound(String(event.data));
      if (parsed.kind === "legacy_token") {
        assembled += parsed.text;
        return;
      }
      if (parsed.kind === "legacy_end") {
        finish(() => resolve(assembled));
        return;
      }
      const { envelope } = parsed;
      switch (envelope.type) {
        case "chat_token": {
          const chunk = textFromChatTokenEnvelope(envelope);
          if (chunk) assembled += chunk;
          break;
        }
        case "chat_done":
          finish(() => resolve(assembled));
          break;
        case "error": {
          const detail = errorDetailFromEnvelope(envelope) ?? "Unknown error";
          finish(() => reject(new Error(detail)));
          break;
        }
        default:
          break;
      }
    };

    ws.onopen = () => {
      ws.send(payload);
    };
  });
}
