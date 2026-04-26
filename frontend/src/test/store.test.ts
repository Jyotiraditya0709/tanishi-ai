import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../features/state/store";
import { apiClient } from "../lib/api/client";

vi.mock("../lib/api/client", () => ({
  apiClient: {
    getStatus: vi.fn(),
    getNotifications: vi.fn(),
    getTasks: vi.fn(),
    getMemory: vi.fn(),
    sendChat: vi.fn(),
    markNotificationsRead: vi.fn(),
  },
}));

describe("app store", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState());
    vi.clearAllMocks();
  });

  it("hydrates runtime data and computes unread count", async () => {
    vi.mocked(apiClient.getStatus).mockResolvedValue({
      brain: { claude: "online", ollama: "offline", tools: 5, default_llm: "auto" },
      autonomy: { enabled_tasks: 2, total_tasks: 4, unread_notifications: 1 },
      timestamp: new Date().toISOString(),
    });
    vi.mocked(apiClient.getNotifications).mockResolvedValue([
      { id: "1", message: "m", priority: "p", source: "s", timestamp: new Date().toISOString(), read: false },
    ]);
    vi.mocked(apiClient.getTasks).mockResolvedValue([]);
    vi.mocked(apiClient.getMemory).mockResolvedValue({});

    await useAppStore.getState().hydrateRuntimeData();
    expect(useAppStore.getState().widget.unreadCount).toBe(1);
    expect(useAppStore.getState().sync.connectionState).toBe("online");
    expect(useAppStore.getState().hud.metrics.cloud).toContain("Claude");
    expect(useAppStore.getState().hud.metrics.tasks).toContain("2/4");
    expect(useAppStore.getState().runtimeStatus?.brain.claude).toBe("online");
  });

  it("runs chat lifecycle and settles to calm", async () => {
    vi.mocked(apiClient.sendChat).mockResolvedValue({
      response: "hello boss",
      model_used: "claude",
    });
    vi.useFakeTimers();

    await useAppStore.getState().submitChat("hi");
    expect(useAppStore.getState().avatar.emotionState).toBe("speaking");
    vi.runAllTimers();
    expect(useAppStore.getState().avatar.targetState).toBe("calm");
    vi.useRealTimers();
  });
});
