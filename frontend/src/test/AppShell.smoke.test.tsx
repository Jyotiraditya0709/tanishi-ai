import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../app/App";
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

describe("App shell smoke", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getStatus).mockResolvedValue({
      brain: { claude: "online", ollama: "offline", tools: 12, default_llm: "auto" },
      autonomy: { enabled_tasks: 1, total_tasks: 3, unread_notifications: 0 },
      timestamp: new Date().toISOString(),
    });
    vi.mocked(apiClient.getNotifications).mockResolvedValue([]);
    vi.mocked(apiClient.getTasks).mockResolvedValue([]);
    vi.mocked(apiClient.getMemory).mockResolvedValue({});
  });

  it("renders mode toolbar and stage after hydrate", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /stage/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /desktop/i })).toBeInTheDocument();
    expect(document.querySelector(".stage")).toBeTruthy();
  });
});
