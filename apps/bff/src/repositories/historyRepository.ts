import { z } from "zod";

export const OrganizedResultSchema = z.object({
  summary: z.string(),
  emotions: z.array(z.string()),
  priorities: z.array(z.string()),
});

export const ActionPlanSchema = z.object({
  title: z.string(),
  steps: z.array(z.string()),
});

export const HistoryItemSchema = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.string(),
  result: OrganizedResultSchema,
  plan: ActionPlanSchema,
});

export type OrganizedResult = z.infer<typeof OrganizedResultSchema>;
export type ActionPlan = z.infer<typeof ActionPlanSchema>;
export type HistoryItem = z.infer<typeof HistoryItemSchema>;

export interface HistoryRepository {
  list(): Promise<HistoryItem[]>;
  save(item: HistoryItem): Promise<HistoryItem>;
}

/**
 * TODO(PoC): 再起動で履歴が消える。本番では Cosmos DB に差し替える。
 */
export class InMemoryHistoryRepository implements HistoryRepository {
  private store: HistoryItem[] = [];

  async list(): Promise<HistoryItem[]> {
    return [...this.store];
  }

  async save(item: HistoryItem): Promise<HistoryItem> {
    this.store.unshift(item);
    return item;
  }
}

export const historyRepository: HistoryRepository = new InMemoryHistoryRepository();
