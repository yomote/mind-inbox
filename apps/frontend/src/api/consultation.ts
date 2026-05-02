import * as mock from "../mockApi";
import type { ActionPlan, ChatMessage, ConsultationSession, OrganizedResult } from "../mockApi";
import { trpc } from "../trpc/client";

const useMock = import.meta.env.VITE_USE_MOCK === "true";

export async function startNewConsultation(concern: string): Promise<ConsultationSession> {
  if (useMock) return mock.startNewConsultation(concern);
  const { session } = await trpc.consultation.start.mutate({ concern });
  return session;
}

export async function sendMessage(sessionId: string, text: string): Promise<ChatMessage> {
  if (useMock) return mock.sendMessage(sessionId, text);
  const res = await trpc.consultation.sendMessage.mutate({
    sessionId,
    message: text,
  });
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    text: res.reply,
    createdAt: new Date().toISOString(),
  };
}

export async function organizeResult(sessionId: string): Promise<OrganizedResult> {
  if (useMock) return mock.organizeResult(sessionId);
  return await trpc.consultation.organize.mutate({ sessionId });
}

export async function createActionPlan(result: OrganizedResult): Promise<ActionPlan> {
  if (useMock) return mock.createActionPlan(result);
  return await trpc.consultation.createPlan.mutate({ result });
}
