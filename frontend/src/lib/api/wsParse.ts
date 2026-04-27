import { wsFrameSchema, type WsFrame } from "./contracts";

export type ParsedWsInbound =
  | { kind: "frame"; frame: WsFrame }
  | { kind: "invalid"; raw: string };

/**
 * Parse one WebSocket text frame from Tanishi `/ws`.
 * Supports JSON frame protocol only.
 */
export function parseWsInbound(raw: string): ParsedWsInbound {
  try {
    const parsed: unknown = JSON.parse(raw);
    const frame = wsFrameSchema.safeParse(parsed);
    if (frame.success) {
      return { kind: "frame", frame: frame.data };
    }
  } catch {
    /* invalid frame */
  }
  return { kind: "invalid", raw };
}
