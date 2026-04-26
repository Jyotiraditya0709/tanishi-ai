import {
  wsChatTokenPayloadSchema,
  wsEnvelopeSchema,
  type WsEnvelope,
} from "./contracts";

export type ParsedWsInbound =
  | { kind: "envelope"; envelope: WsEnvelope }
  | { kind: "legacy_end" }
  | { kind: "legacy_token"; text: string };

/**
 * Parse one WebSocket text frame from Tanishi `/ws`.
 * Supports JSON envelopes (v2 protocol) and legacy plain token stream + `[END]`.
 */
export function parseWsInbound(raw: string): ParsedWsInbound {
  const trimmed = raw.trim();
  if (trimmed === "[END]") {
    return { kind: "legacy_end" };
  }
  if (trimmed.startsWith("{")) {
    try {
      const parsed: unknown = JSON.parse(raw);
      const env = wsEnvelopeSchema.safeParse(parsed);
      if (env.success) {
        return { kind: "envelope", envelope: env.data };
      }
    } catch {
      /* fall through to legacy token */
    }
  }
  return { kind: "legacy_token", text: raw };
}

export function textFromChatTokenEnvelope(envelope: WsEnvelope): string | null {
  if (envelope.type !== "chat_token") return null;
  const parsed = wsChatTokenPayloadSchema.safeParse(envelope.payload);
  return parsed.success ? parsed.data.text : null;
}

export function errorDetailFromEnvelope(envelope: WsEnvelope): string | null {
  if (envelope.type !== "error") return null;
  if (typeof envelope.payload === "object" && envelope.payload !== null && "detail" in envelope.payload) {
    const d = (envelope.payload as { detail?: unknown }).detail;
    return typeof d === "string" ? d : JSON.stringify(envelope.payload);
  }
  return JSON.stringify(envelope.payload);
}
