import { z } from "zod";

export const chatRequestSchema = z.object({
  message: z.string().min(1),
  mood: z.string().optional(),
  session_id: z.string().optional(),
});

export const chatResponseSchema = z.object({
  response: z.string(),
  model_used: z.string(),
  tokens_in: z.number().optional(),
  tokens_out: z.number().optional(),
  tools_used: z.array(z.unknown()).optional(),
  session_id: z.string().optional(),
  timestamp: z.string().optional(),
});

export const statusSchema = z.object({
  brain: z.record(z.string(), z.unknown()).default({}),
  autonomy: z.record(z.string(), z.unknown()).default({}),
  timestamp: z.string(),
});

export const memorySchema = z.object({
  stats: z
    .object({
      core_memories: z.number().optional(),
      total_memories: z.number().optional(),
    })
    .optional(),
  core: z.record(z.string(), z.string()).optional(),
  recent: z
    .array(
      z.object({
        content: z.string(),
        category: z.string(),
        importance: z.number(),
      }),
    )
    .optional(),
});

export const taskSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  interval_minutes: z.number(),
  enabled: z.boolean(),
  last_run: z.string().nullable().optional(),
  run_count: z.number(),
});

export const notificationSchema = z.object({
  id: z.string(),
  message: z.string(),
  priority: z.string(),
  source: z.string(),
  timestamp: z.string(),
  read: z.boolean(),
});

export const wsEnvelopeSchema = z.object({
  type: z.enum(["status", "chat_token", "chat_done", "notification", "error"]),
  timestamp: z.string(),
  payload: z.unknown(),
});

/** Outbound WebSocket v2 handshake (client → server). */
export const wsClientV2HandshakeSchema = z.object({
  protocol: z.literal("v2"),
  message: z.string().min(1),
});

export const wsChatTokenPayloadSchema = z.object({
  text: z.string(),
});

export const wsErrorPayloadSchema = z.object({
  detail: z.string(),
});

export const transcriptMessageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant", "system"]),
  text: z.string(),
  createdAt: z.string(),
  stateTag: z.string().optional(),
  pending: z.boolean().optional(),
  error: z.string().optional(),
});

export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type StatusResponse = z.infer<typeof statusSchema>;
export type MemoryResponse = z.infer<typeof memorySchema>;
export type Task = z.infer<typeof taskSchema>;
export type NotificationItem = z.infer<typeof notificationSchema>;
export type WsEnvelope = z.infer<typeof wsEnvelopeSchema>;
export type WsClientV2Handshake = z.infer<typeof wsClientV2HandshakeSchema>;
export type TranscriptMessage = z.infer<typeof transcriptMessageSchema>;
