import { create } from "zustand";
import { apiClient } from "../../lib/api/client";
import { buildHudFromStatus } from "../../lib/api/hudMetrics";
import { streamThinkOverWs } from "../../lib/api/wsStreamThink";
import type {
  MemoryResponse,
  NotificationItem,
  StatusResponse,
  TranscriptMessage,
  Task,
} from "../../lib/api/contracts";
import { speakAssistantReply } from "../../lib/voice/browserSpeak";
import { tanishiStates, type TanishiStateKey } from "../avatar/states";

type Mode = "stage" | "widget" | "takeover";
type ConnectionState = "idle" | "connecting" | "online" | "error";

type AppState = {
  ui: {
    mode: Mode;
    panelVisibility: { hud: boolean; caption: boolean; readout: boolean };
    isTransitioning: boolean;
  };
  chat: {
    messages: TranscriptMessage[];
    isThinking: boolean;
    lastError: string | null;
    streamingState: "idle" | "thinking" | "speaking";
  };
  avatar: {
    emotionState: TanishiStateKey;
    speakingLevel: number;
    targetState: TanishiStateKey;
    transitionProgress: number;
  };
  hud: { enabled: boolean; metrics: Record<string, string>; alertLevel: "low" | "medium" | "high" };
  widget: {
    visible: boolean;
    compactState: "idle" | "expanded";
    unreadCount: number;
    quickActions: string[];
    previewTitle: string;
    previewBody: string;
  };
  sync: { activeTurnId: string | null; lastEventTs: string | null; connectionState: ConnectionState };
  tasks: Task[];
  notifications: NotificationItem[];
  memory: MemoryResponse | null;
  /** Last full `/status` payload for HUD + diagnostics. */
  runtimeStatus: StatusResponse | null;
  setMode: (mode: Mode) => void;
  setEmotionState: (state: TanishiStateKey) => void;
  setPanelVisibility: (key: "hud" | "caption" | "readout", value: boolean) => void;
  hydrateRuntimeData: () => Promise<void>;
  submitChat: (text: string) => Promise<void>;
  markRead: () => Promise<void>;
};

function msg(role: TranscriptMessage["role"], text: string, patch?: Partial<TranscriptMessage>): TranscriptMessage {
  return {
    id: crypto.randomUUID(),
    role,
    text,
    createdAt: new Date().toISOString(),
    ...patch,
  };
}

function isWsChatTransport(): boolean {
  return import.meta.env.VITE_CHAT_TRANSPORT === "ws";
}

function applyAssistantSuccess(text: string, set: (fn: (s: AppState) => Partial<AppState>) => void) {
  set((s) => {
    const messages = s.chat.messages.filter((m) => !m.pending);
    return {
      chat: {
        ...s.chat,
        messages: [...messages, msg("assistant", text, { stateTag: "speaking" })],
        isThinking: false,
        streamingState: "speaking",
      },
      avatar: { ...s.avatar, emotionState: "speaking", targetState: "speaking", speakingLevel: 0.8 },
    };
  });
  window.setTimeout(() => {
    set((s) => ({
      chat: { ...s.chat, streamingState: "idle" },
      avatar: { ...s.avatar, emotionState: "calm", targetState: "calm", speakingLevel: 0 },
    }));
  }, 1300);
  queueMicrotask(() => speakAssistantReply(text));
}

export const useAppStore = create<AppState>((set, get) => ({
  ui: {
    mode: "stage",
    panelVisibility: { hud: true, caption: true, readout: true },
    isTransitioning: false,
  },
  chat: {
    messages: [
      msg("assistant", "Good morning, boss. Overnight runs complete. Kept 5 improvements.", {
        stateTag: "briefing",
      }),
    ],
    isThinking: false,
    lastError: null,
    streamingState: "idle",
  },
  avatar: {
    emotionState: "calm",
    speakingLevel: 0,
    targetState: "calm",
    transitionProgress: 1,
  },
  hud: { enabled: true, ...buildHudFromStatus(null) },
  widget: {
    visible: true,
    compactState: "idle",
    unreadCount: 0,
    quickActions: ["Reply", "Snooze", "Mute"],
    previewTitle: "Alerts",
    previewBody: "No notifications yet. Enable background tasks for proactive alerts.",
  },
  sync: { activeTurnId: null, lastEventTs: null, connectionState: "idle" },
  tasks: [],
  notifications: [],
  memory: null,
  runtimeStatus: null,
  setMode: (mode) => set((s) => ({ ui: { ...s.ui, mode } })),
  setEmotionState: (state) =>
    set((s) => ({
      avatar: { ...s.avatar, emotionState: state, targetState: state, transitionProgress: 0 },
    })),
  setPanelVisibility: (key, value) =>
    set((s) => ({ ui: { ...s.ui, panelVisibility: { ...s.ui.panelVisibility, [key]: value } } })),
  hydrateRuntimeData: async () => {
    try {
      const [status, notifications, tasks, memory] = await Promise.all([
        apiClient.getStatus(),
        apiClient.getNotifications(),
        apiClient.getTasks(),
        apiClient.getMemory(),
      ]);
      const unreadList = notifications.filter((n) => !n.read);
      const previewBody =
        unreadList.length > 0
          ? unreadList[unreadList.length - 1].message
          : notifications.length > 0
            ? notifications[notifications.length - 1].message
            : "No notifications yet. Enable background tasks for proactive alerts.";
      const previewTitle =
        unreadList.length > 0 ? "Unread" : notifications.length > 0 ? "Latest" : "Alerts";
      const hudDerived = buildHudFromStatus(status);

      set((s) => ({
        runtimeStatus: status,
        notifications,
        tasks,
        memory,
        hud: { enabled: s.hud.enabled, ...hudDerived },
        widget: {
          ...s.widget,
          unreadCount: unreadList.length,
          previewTitle,
          previewBody,
        },
        sync: { ...s.sync, connectionState: "online", lastEventTs: status.timestamp },
      }));
    } catch {
      set((s) => ({ sync: { ...s.sync, connectionState: "error", lastEventTs: new Date().toISOString() } }));
    }
  },
  submitChat: async (text) => {
    const trimmed = text.trim();
    if (!trimmed || get().chat.isThinking) return;

    const turnId = crypto.randomUUID();
    set((s) => ({
      chat: {
        ...s.chat,
        messages: [
          ...s.chat.messages,
          msg("user", trimmed),
          msg("assistant", "", { pending: true, stateTag: "thinking" }),
        ],
        isThinking: true,
        streamingState: "thinking",
        lastError: null,
      },
      avatar: { ...s.avatar, emotionState: "thinking", targetState: "thinking" },
      sync: { ...s.sync, activeTurnId: turnId, lastEventTs: new Date().toISOString() },
    }));

    try {
      if (isWsChatTransport()) {
        const reply = await streamThinkOverWs(trimmed);
        applyAssistantSuccess(reply, set);
      } else {
        const response = await apiClient.sendChat({ message: trimmed });
        applyAssistantSuccess(response.response, set);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      set((s) => ({
        chat: {
          ...s.chat,
          messages: [
            ...s.chat.messages.filter((m) => !m.pending),
            msg("assistant", "Cloud link flaky. Running local fallback, boss.", { error: message, stateTag: "alert" }),
          ],
          isThinking: false,
          lastError: message,
          streamingState: "idle",
        },
        avatar: { ...s.avatar, emotionState: "alert", targetState: "alert", speakingLevel: 0 },
      }));
      window.setTimeout(() => {
        set((s) => ({ avatar: { ...s.avatar, emotionState: "calm", targetState: "calm" } }));
      }, 1100);
    }
  },
  markRead: async () => {
    await apiClient.markNotificationsRead();
    const notifications = get().notifications.map((item) => ({ ...item, read: true }));
    set((s) => ({
      notifications,
      widget: {
        ...s.widget,
        unreadCount: 0,
        previewTitle: "Latest",
        previewBody:
          notifications.length > 0
            ? notifications[notifications.length - 1].message
            : "No notifications yet. Enable background tasks for proactive alerts.",
      },
    }));
  },
}));

export const stateCaption = (state: TanishiStateKey): string => tanishiStates[state].caption;
