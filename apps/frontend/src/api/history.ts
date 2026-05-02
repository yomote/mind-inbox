import * as mock from "../mockApi";
import type { ActionPlan, HistoryItem, OrganizedResult } from "../mockApi";
import { trpc } from "../trpc/client";

const useMock = import.meta.env.VITE_USE_MOCK === "true";

export async function loadHistories(): Promise<HistoryItem[]> {
  if (useMock) return mock.loadHistories();
  return await trpc.history.list.query();
}

export async function saveHistory(input: {
  sessionId: string;
  title: string;
  result: OrganizedResult;
  plan: ActionPlan;
}): Promise<HistoryItem> {
  if (useMock) return mock.saveHistory(input);
  return await trpc.history.save.mutate(input);
}
