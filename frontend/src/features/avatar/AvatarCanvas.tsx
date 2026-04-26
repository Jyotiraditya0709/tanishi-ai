import { memo, useEffect, useRef } from "react";
import { tanishiStates, type TanishiStateKey } from "./states";

type AvatarCanvasProps = {
  state: TanishiStateKey;
  speakingLevel: number;
};

export const AvatarCanvas = memo(function AvatarCanvas({ state, speakingLevel }: AvatarCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    let frame = 0;
    let animation = 0;
    const config = tanishiStates[state];
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const draw = () => {
      frame += 1;
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const radius = Math.min(w, h) * 0.19;
      const motion = config.motion + speakingLevel * 0.25;
      const pulse = 1 + Math.sin(frame / 24) * (0.08 + motion * 0.06);
      const hue = 58 + config.hueShift;

      context.clearRect(0, 0, w, h);
      context.fillStyle = "rgba(11, 10, 8, 0.06)";
      context.fillRect(0, 0, w, h);

      context.save();
      context.translate(cx, cy);

      for (let ring = 0; ring < 3; ring += 1) {
        context.beginPath();
        context.arc(0, 0, radius * pulse * (1 + ring * 0.34), 0, Math.PI * 2);
        context.strokeStyle = `oklch(0.82 0.13 ${hue} / ${0.24 - ring * 0.06})`;
        context.lineWidth = dpr * (2 - ring * 0.4);
        context.stroke();
      }

      const spokes = 56;
      for (let i = 0; i < spokes; i += 1) {
        const angle = ((Math.PI * 2) / spokes) * i + frame * 0.003 * motion;
        const inner = radius * 0.6;
        const outer = radius * (1.1 + 0.25 * Math.sin(frame / 30 + i * 0.45));
        context.beginPath();
        context.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
        context.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer);
        context.strokeStyle = `oklch(0.82 0.12 ${hue} / 0.34)`;
        context.lineWidth = dpr * 0.8;
        context.stroke();
      }

      context.beginPath();
      context.arc(0, 0, radius * 0.4, 0, Math.PI * 2);
      context.fillStyle = `oklch(0.95 0.08 ${hue} / 0.88)`;
      context.fill();
      context.restore();

      animation = requestAnimationFrame(draw);
    };

    animation = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(animation);
      observer.disconnect();
    };
  }, [state, speakingLevel]);

  return <canvas ref={ref} className="avatar-canvas" aria-label="Tanishi avatar" />;
});
