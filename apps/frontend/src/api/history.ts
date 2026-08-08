import * as mock from "../mockApi";
import type { ActionPlan, HistoryItem, OrganizedResult } from "./types";
import { trpc } from "../trpc/client";
import { useMock } from "./http";

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
