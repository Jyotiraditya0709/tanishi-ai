import { useEffect, useMemo, useRef, useState } from "react";

declare global {
  interface Window {
    mermaid?: {
      initialize: (opts: Record<string, unknown>) => void;
      render: (
        id: string,
        definition: string,
      ) => Promise<{ svg: string } | string>;
    };
    __tanishiMermaidReady?: Promise<void>;
    Chart?: new (
      ctx: CanvasRenderingContext2D,
      config: Record<string, unknown>,
    ) => {
      destroy?: () => void;
    };
    __tanishiChartReady?: Promise<void>;
  }
}

type CanvasItem = {
  kind: "mermaid" | "chart" | "html";
  payload: string;
};

async function ensureMermaidLoaded(): Promise<void> {
  if (typeof window === "undefined") return;
  if (window.mermaid) return;
  if (!window.__tanishiMermaidReady) {
    window.__tanishiMermaidReady = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js";
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("Failed to load mermaid"));
      document.head.appendChild(script);
    });
  }
  await window.__tanishiMermaidReady;
}

async function ensureChartLoaded(): Promise<void> {
  if (typeof window === "undefined") return;
  if (window.Chart) return;
  if (!window.__tanishiChartReady) {
    window.__tanishiChartReady = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/chart.js";
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("Failed to load Chart.js"));
      document.head.appendChild(script);
    });
  }
  await window.__tanishiChartReady;
}

function MermaidCard({ payload }: { payload: string }) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");
  const renderId = useMemo(() => `tanishi-mermaid-${crypto.randomUUID()}`, []);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        await ensureMermaidLoaded();
        if (!window.mermaid) throw new Error("Mermaid unavailable");
        window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        const out = await window.mermaid.render(renderId, payload);
        const svgOut = typeof out === "string" ? out : out.svg;
        if (mounted) setSvg(svgOut);
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : "Mermaid render failed");
      }
    })();
    return () => {
      mounted = false;
    };
  }, [payload, renderId]);

  if (error) {
    return <div className="canvas-card canvas-card--error">Mermaid error: {error}</div>;
  }
  if (!svg) {
    return <div className="canvas-card">Rendering diagram...</div>;
  }
  return (
    <div className="canvas-card">
      <div className="canvas-label">Diagram</div>
      <div className="canvas-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  );
}

function ChartCard({ payload }: { payload: string }) {
  const [error, setError] = useState<string>("");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let mounted = true;
    let chart: { destroy?: () => void } | null = null;
    (async () => {
      try {
        await ensureChartLoaded();
        if (!window.Chart) throw new Error("Chart.js unavailable");
        const parsed = JSON.parse(payload) as Record<string, unknown>;
        const canvas = canvasRef.current;
        if (!canvas) throw new Error("Canvas mount failed");
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("Unable to get 2D context");
        chart = new window.Chart(ctx, parsed);
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : "Chart render failed");
      }
    })();
    return () => {
      mounted = false;
      try {
        chart?.destroy?.();
      } catch {
        /* noop */
      }
    };
  }, [payload, canvasRef]);

  if (error) {
    return <div className="canvas-card canvas-card--error">Chart error: {error}</div>;
  }
  return (
    <div className="canvas-card">
      <div className="canvas-label">Chart</div>
      <div className="canvas-chart">
        <canvas
          ref={(el) => {
            canvasRef.current = el;
          }}
        />
      </div>
    </div>
  );
}

function HtmlCard({ payload }: { payload: string }) {
  return (
    <div className="canvas-card">
      <div className="canvas-label">Interactive</div>
      <iframe
        className="canvas-html"
        sandbox="allow-scripts"
        srcDoc={payload}
        title="Tanishi Canvas HTML"
      />
    </div>
  );
}

export function CanvasView({ canvas }: { canvas: CanvasItem }) {
  if (canvas.kind === "mermaid") {
    return <MermaidCard payload={canvas.payload} />;
  }
  if (canvas.kind === "chart") {
    return <ChartCard payload={canvas.payload} />;
  }
  if (canvas.kind === "html") {
    return <HtmlCard payload={canvas.payload} />;
  }
  return (
    <div className="canvas-card">
      <div className="canvas-label">Canvas ({canvas.kind})</div>
      <div>Unsupported canvas kind.</div>
    </div>
  );
}
