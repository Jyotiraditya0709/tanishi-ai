import { describe, expect, it } from "vitest";
import { errorDetailFromEnvelope, parseWsInbound, textFromChatTokenEnvelope } from "../lib/api/wsParse";

describe("parseWsInbound", () => {
  it("detects legacy end marker", () => {
    expect(parseWsInbound("[END]")).toEqual({ kind: "legacy_end" });
  });

  it("parses JSON envelope", () => {
    const raw = JSON.stringify({
      type: "chat_token",
      timestamp: "2026-01-01T00:00:00",
      payload: { text: "hi" },
    });
    const r = parseWsInbound(raw);
    expect(r.kind).toBe("envelope");
    if (r.kind === "envelope") {
      expect(r.envelope.type).toBe("chat_token");
      expect(textFromChatTokenEnvelope(r.envelope)).toBe("hi");
    }
  });

  it("treats invalid JSON object as legacy token", () => {
    const r = parseWsInbound("{not json");
    expect(r).toEqual({ kind: "legacy_token", text: "{not json" });
  });

  it("extracts error detail", () => {
    const env = {
      type: "error" as const,
      timestamp: "t",
      payload: { detail: "boom" },
    };
    expect(errorDetailFromEnvelope(env)).toBe("boom");
  });
});
