export type TanishiStateKey =
  | "calm"
  | "alert"
  | "caring"
  | "briefing"
  | "annoyed"
  | "speaking"
  | "thinking"
  | "evolving";

export type TanishiStateConfig = {
  label: string;
  code: string;
  caption: string;
  kicker: string;
  energy: number;
  hueShift: number;
  motion: number;
};

export const stateOrder: TanishiStateKey[] = [
  "calm",
  "alert",
  "caring",
  "briefing",
  "annoyed",
  "speaking",
  "thinking",
  "evolving",
];

export const tanishiStates: Record<TanishiStateKey, TanishiStateConfig> = {
  calm: { label: "Calm", code: "IDLE.RESTING", caption: "Here, boss. Breathing.", kicker: "IDLE", energy: 0.2, hueShift: 0, motion: 0.4 },
  alert: { label: "Alert", code: "ALERT.SHARP", caption: "Plug in or I lose you, boss.", kicker: "ALERT", energy: 0.95, hueShift: -42, motion: 1 },
  caring: { label: "Caring", code: "WARM.SOFT", caption: "Stand up. Drink water. I will still be here.", kicker: "CARE", energy: 0.25, hueShift: -20, motion: 0.2 },
  briefing: { label: "Briefing", code: "BRIEF.SCAN", caption: "112 experiments overnight. Kept 5.", kicker: "BRIEF", energy: 0.55, hueShift: 8, motion: 0.6 },
  annoyed: { label: "Annoyed", code: "CHIDE.MILD", caption: "I told you twice already, sir.", kicker: "CHIDE", energy: 0.7, hueShift: -55, motion: 0.8 },
  speaking: { label: "Speaking", code: "VOX.ACTIVE", caption: "I am an AI. I know the time.", kicker: "SPEAKING", energy: 0.8, hueShift: 0, motion: 0.9 },
  thinking: { label: "Thinking", code: "PROC.DEEP", caption: "Hold on. Chasing a thread.", kicker: "THINKING", energy: 0.6, hueShift: 6, motion: 0.7 },
  evolving: { label: "Self-improving", code: "SELF.OPTIMIZE", caption: "While you sleep, I get better.", kicker: "EVOLVE", energy: 0.5, hueShift: 12, motion: 0.5 },
};
