/**
 * [L1] mockApi の出力 shape と分岐挙動を検証する。
 *
 * ここで test しないこと:
 *   - BFF zod schema との一致 (それは L0 contract test の領域。ここでは frontend 内部での
 *     "mockApi が export する type と実際の戻り値の shape の整合" だけを見る)
 *   - コンポーネント render (snapshot 最小原則。L3 が golden path で通し検証する)
 *   - hook の単体検証 (L2 service test と重複するので avoid)
 *   - `wait()` の遅延検証 (mock 用の cosmetic)
 */

import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  loadHistories,
  organizeResult,
  sendMessage,
  type ActionPlan,
  type HistoryItem,
  type OrganizedResult,
} from "./mockApi";

// ── frontend 内 type と shape を縛る zod schema ──────────────────────────────
// mockApi.ts が export している type 宣言と同じ shape を runtime でも縛る。
// type だけだと TypeScript の structural typing で「未宣言の余分なキー」を許してしまう。
// ここで zod parse することで「mock data に新フィールド追加」「型変更」「null 混入」など
// type 越しに静かに通る変更を捕まえる。
const OrganizedResultSchema = z.object({
  summary: z.string(),
  emotions: z.array(z.string()),
  priorities: z.array(z.string()),
}) satisfies z.ZodType<OrganizedResult>;

const ActionPlanSchema = z.object({
  title: z.string(),
  steps: z.array(z.string()),
}) satisfies z.ZodType<ActionPlan>;

const HistoryItemSchema = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.string(),
  result: OrganizedResultSchema,
  plan: ActionPlanSchema,
}) satisfies z.ZodType<HistoryItem>;

describe("[L1] mockApi shape integrity", () => {
  // 無いと何が静かに通るか: organizeResult の戻り値に新フィールド追加 / 既存フィールド型変更が
  // type だけだと許される。runtime parse で気づく。
  it("organizeResult が OrganizedResult schema に一致する", async () => {
    const result = await organizeResult("dummy-session-id");
    expect(() => OrganizedResultSchema.parse(result)).not.toThrow();
    // strategy.md §1.2 の「assert は意図のあるところだけ」原則: parse 通過 + 必須要素存在
    expect(result.priorities.length).toBeGreaterThan(0);
  });

  // 無いと何が静かに通るか: loadHistories の mock データを更新するときに HistoryItem の必須
  // フィールドを欠落させても type 推論だけだと気づかない。parse で確実に弾く。
  it("loadHistories の各 item が HistoryItem schema に一致する", async () => {
    const items = await loadHistories();
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) {
      expect(() => HistoryItemSchema.parse(item)).not.toThrow();
    }
  });
});

describe("[L1] mockApi.sendMessage 応答長分岐", () => {
  // 無いと何が静かに通るか: text.length > 45 の閾値分岐ロジック自体は意図ある仕様で、
  // 間違って常に同じ reply を返すリファクタが入ったら気づきたい。
  it("短い入力には日常影響を尋ねる reply、長い入力には場面の深掘り reply を返す", async () => {
    const short = await sendMessage("s", "短いメッセージ");
    const long = await sendMessage("s", "あ".repeat(60));

    expect(short.text).toContain("日常へどんな影響");
    expect(long.text).toContain("特に気持ちが動いた場面");
    expect(short.text).not.toBe(long.text);
  });
});
