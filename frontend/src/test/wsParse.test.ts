import { describe, expect, it } from "vitest";
import { parseWsInbound } from "../lib/api/wsParse";

describe("parseWsInbound", () => {
  it("parses JSON chunk frame", () => {
    const raw = JSON.stringify({
      type: "chunk",
      text: "hi",
    });
    const r = parseWsInbound(raw);
    expect(r.kind).toBe("frame");
    if (r.kind === "frame") {
      expect(r.frame.type).toBe("chunk");
      if (r.frame.type === "chunk") {
        expect(r.frame.text).toBe("hi");
      }
    }
  });

  it("marks invalid payload as invalid", () => {
    const r = parseWsInbound("{not json");
    expect(r).toEqual({ kind: "invalid", raw: "{not json" });
  });

  it("parses canvas and end frames", () => {
    const c = parseWsInbound(JSON.stringify({ type: "canvas", kind: "mermaid", payload: "graph TD;A-->B" }));
    expect(c.kind).toBe("frame");
    const e = parseWsInbound(JSON.stringify({ type: "end" }));
    expect(e.kind).toBe("frame");
  });
});
