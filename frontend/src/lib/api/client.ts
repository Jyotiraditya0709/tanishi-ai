import {
  chatRequestSchema,
  chatResponseSchema,
  memorySchema,
  notificationSchema,
  statusSchema,
  taskSchema,
  type ChatRequest,
  type ChatResponse,
  type MemoryResponse,
  type NotificationItem,
  type StatusResponse,
  type Task,
} from "./contracts";
import { apiUrl } from "./apiBase";

async function parseJson<T>(res: Response, parser: (value: unknown) => T): Promise<T> {
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  const payload = await res.json();
  return parser(payload);
}

export const apiClient = {
  async getStatus(): Promise<StatusResponse> {
    const res = await fetch(apiUrl("/status"));
    return parseJson(res, (value) => statusSchema.parse(value));
  },

  async sendChat(request: ChatRequest): Promise<ChatResponse> {
    const body = chatRequestSchema.parse(request);
    const res = await fetch(apiUrl("/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return parseJson(res, (value) => chatResponseSchema.parse(value));
  },

  async getMemory(): Promise<MemoryResponse> {
    const res = await fetch(apiUrl("/memory"));
    return parseJson(res, (value) => memorySchema.parse(value));
  },

  async getTasks(): Promise<Task[]> {
    const res = await fetch(apiUrl("/tasks"));
    return parseJson(res, (value) => taskSchema.array().parse(value));
  },

  async getNotifications(): Promise<NotificationItem[]> {
    const res = await fetch(apiUrl("/notifications"));
    return parseJson(res, (value) => notificationSchema.array().parse(value));
  },

  async markNotificationsRead(): Promise<void> {
    const res = await fetch(apiUrl("/notifications/read"), { method: "POST" });
    if (!res.ok) {
      throw new Error(`Failed to mark notifications: ${res.status}`);
    }
  },
};
