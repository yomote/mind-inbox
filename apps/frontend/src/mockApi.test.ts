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

import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetProblemStore,
  commitPreview,
  createProblemPlan,
  extractMentions,
  loadProblem,
  loadProblems,
  previewExtraction,
  sendMessage,
  triageProblem,
} from "./mockApi";
// Problem 系の shape は domain.ts（BFF = 型の真実 / CLAUDE.md §8.3）の zod schema を
// 直接 import して縛る。inline コピーだと .int()/.min(1) などランタイムバリデータの
// 乖離をこのテストが検知できなくなる（PR #44 レビュー指摘）。
import { ExtractionResultSchema, ProblemSchema } from "../../bff/src/trpc/domain";

// ── frontend 内 type と shape を縛る zod schema ──────────────────────────────
// mockApi.ts が export している type 宣言と同じ shape を runtime でも縛る。
// type だけだと TypeScript の structural typing で「未宣言の余分なキー」を許してしまう。
// ここで zod parse することで「mock data に新フィールド追加」「型変更」「null 混入」など
// type 越しに静かに通る変更を捕まえる。
describe("[L1] mockApi Problem shape & 不変条件", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: seed データに必須フィールド欠落 / 型変更 / mentions 空配列が混入しても
  // type だけだと気づかない。parse + min(1) で domain_model §2.2「mentions は最低1」を縛る。
  it("loadProblems の各 Problem が schema に一致し mentionCount = mentions.length", async () => {
    const problems = await loadProblems();
    expect(problems.length).toBeGreaterThan(0);
    for (const p of problems) {
      expect(() => ProblemSchema.parse(p)).not.toThrow();
      expect(p.mentionCount).toBe(p.mentions.length);
    }
  });

  // 無いと何が静かに通るか: テーマ分布 / 再出現 / 棚卸し済みを網羅する seed が痩せても気づかない
  // （D3 完了条件のデータ網羅性を縛る）。
  it("seed が 再出現(複数mention) / 棚卸し済み(resolved・shelved) を含む", async () => {
    const problems = await loadProblems();
    expect(problems.some((p) => p.mentionCount >= 2)).toBe(true);
    expect(problems.some((p) => p.status === "resolved")).toBe(true);
    expect(problems.some((p) => p.status === "shelved")).toBe(true);
  });
});

describe("[L1] mockApi.extractMentions グルーピング", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: 抽出が「新規 / 既存への再出現」の両方を返す挙動は UC-01/03 の核。
  // 常に新規だけ返すリグレッションを捕まえる。
  it("新規 Problem と 既存への再出現(🔁) の両方を返す", async () => {
    const res = await extractMentions("s-test");
    expect(res.items).toHaveLength(2);
    expect(res.items.some((i) => i.grouping.kind === "new")).toBe(true);
    const recurrence = res.items.find((i) => i.grouping.kind === "existing");
    expect(recurrence?.grouping.isRecurrence).toBe(true);
    expect(recurrence?.grouping.mentionCount).toBeGreaterThan(1);
  });

  // 無いと何が静かに通るか: extract が store を書き換えず一覧に反映されないと「一周」が成立しない。
  it("抽出後に store が更新され、新規 Problem が一覧に増える", async () => {
    const before = await loadProblems();
    await extractMentions("s-test");
    const after = await loadProblems();
    expect(after.length).toBe(before.length + 1);
  });
});

const userMsg = (i: number) => ({
  id: `u-${i}`,
  role: "user" as const,
  text: `ユーザー発話 ${i} — 最近いろいろ考えてしまって眠れない`,
  createdAt: new Date().toISOString(),
});
const aiMsg = (i: number) => ({
  id: `a-${i}`,
  role: "assistant" as const,
  text: "受け止めました。",
  createdAt: new Date().toISOString(),
});
const conversation = (userCount: number) =>
  Array.from({ length: userCount }, (_, i) => [userMsg(i), aiMsg(i)]).flat();

describe("[単体] mockApi.previewExtraction — 読み取り専用の下書き (#187 / ADR 0039 D1)", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: preview が store を書いても例外は出ず、確定していない下書きが
  // Problem 一覧に静かに現れる (ADR 0039 の核「揮発する下書き / 無垢な DB」が壊れる)。
  it("preview を何度呼んでも Problem store は変化しない", async () => {
    const before = await loadProblems();
    await previewExtraction("s-preview", conversation(2));
    await previewExtraction("s-preview", conversation(4));
    const after = await loadProblems();
    expect(after).toEqual(before);
  });

  // 無いと何が静かに通るか: 「会話が進むと下書きが増える / 育つ」の可視化 (#187 の体験の核) が
  // 常に同じ 1 枚を返す実装に退行しても、画面は描画され続けて気づけない。
  it("会話が進むほど下書きは単調に増え、既存への再出現 (🔁) と新規の両方が現れる", async () => {
    let prevCount = 0;
    for (const userCount of [0, 1, 2, 3, 4]) {
      const result = await previewExtraction("s-preview", conversation(userCount));
      expect(() => ExtractionResultSchema.parse(result)).not.toThrow();
      expect(result.items.length).toBeGreaterThanOrEqual(prevCount);
      prevCount = result.items.length;
    }
    const full = await previewExtraction("s-preview", conversation(4));
    expect(full.items.some((i) => i.grouping.kind === "existing" && i.grouping.isRecurrence)).toBe(
      true,
    );
    expect(full.items.some((i) => i.grouping.kind === "new")).toBe(true);
    expect(full.newProblemCount).toBe(1);
    expect(full.updatedProblemCount).toBe(1);
  });

  // 無いと何が静かに通るか: 下書きカードの excerpt が固定文言に退行すると「自分の言葉が
  // 形になっていく」来歴表示が失われる (extract-review.mdx の excerpt の意図と同じ)。
  it("下書きの excerpt は実際のユーザー発話から引用される", async () => {
    const messages = conversation(1);
    const result = await previewExtraction("s-preview", messages);
    expect(result.items[0].mention.excerpt).toBe(messages[0].text.slice(0, 60));
  });
});

describe("[単体] mockApi.commitPreview — 表示中の下書きをそのまま保存する (#187 / PR #282 P1)", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: 確定が「再抽出」に退行しても例外は出ず、画面で確認した内容と
  // 違う Problem が静かに保存される (ADR 0039 D1「この内容で確定」の約束が壊れる)。
  // 2 往復時点の下書きは 1 件 — 確定で保存されるのもその 1 件だけでなければならない
  // (extractMentions は常に 2 件書くので、再抽出経路ならこのテストが落ちる)。
  it("1 件の下書きの確定では、その 1 件だけが保存される (新規 Problem は増えない)", async () => {
    const drafts = (await previewExtraction("s-commit", conversation(2))).items;
    expect(drafts).toHaveLength(1);
    const before = await loadProblems();

    const committed = await commitPreview("s-commit", drafts);

    const after = await loadProblems();
    expect(after.length).toBe(before.length);
    expect(committed.items).toHaveLength(1);
    expect(committed.newProblemCount).toBe(0);
    expect(committed.updatedProblemCount).toBe(1);
    const career = await loadProblem("p-career");
    expect(career?.mentionCount).toBe(4);
    expect(career?.mentions.at(-1)?.excerpt).toBe(drafts[0].mention.excerpt);
  });

  // 無いと何が静かに通るか: 新規下書きの確定が下書きと違う内容 (タイトル / 引用) で
  // Problem を起こしても気づけない。「あなたがこう言ったやつ」の来歴が壊れる。
  it("新規 + 既存の下書きを確定すると、下書きの内容どおりに書かれる", async () => {
    const drafts = (await previewExtraction("s-commit", conversation(4))).items;
    expect(drafts).toHaveLength(2);
    const before = await loadProblems();

    const committed = await commitPreview("s-commit", drafts);

    const after = await loadProblems();
    expect(after.length).toBe(before.length + 1);
    expect(() => ExtractionResultSchema.parse(committed)).not.toThrow();

    const newDraft = drafts.find((d) => d.grouping.kind === "new");
    const created = await loadProblem(newDraft?.grouping.problemId ?? "");
    expect(created?.title).toBe(newDraft?.grouping.problemTitle);
    expect(created?.mentions[0]?.excerpt).toBe(newDraft?.mention.excerpt);
  });
});

describe("[L1] mockApi.triageProblem 状態遷移", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: 棚卸し（resolve/shelve）と再オープンの遷移は UC-04/03 の本体。
  // 遷移しない / 日時を記録しないリグレッションを捕まえる。
  it("resolve → reopen で status と resolvedAt が往復する", async () => {
    const resolved = await triageProblem({ action: "resolve", problemId: "p-career" });
    expect(resolved?.status).toBe("resolved");
    expect(resolved?.resolvedAt).not.toBeNull();

    const reopened = await triageProblem({ action: "reopen", problemId: "p-career" });
    expect(reopened?.status).toBe("open");
    expect(reopened?.resolvedAt).toBeNull();
  });

  // 無いと何が静かに通るか: dismiss は store から取り除く（誤抽出の取り下げ）。残ると却下が効かない。
  it("dismiss で Problem が store から消える", async () => {
    const target = (await loadProblems())[0];
    const result = await triageProblem({ action: "dismiss", problemId: target.id });
    expect(result).toBeNull();
    expect(await loadProblem(target.id)).toBeNull();
  });

  // 無いと何が静かに通るか: editTheme がテーマを書き換える（トリアージで主テーマ付け替え）。
  it("editTheme で主テーマが付け替わる", async () => {
    const updated = await triageProblem({
      action: "editTheme",
      problemId: "p-career",
      theme: "お金",
    });
    expect(updated?.theme).toBe("お金");
  });
});

describe("[L1] mockApi.createProblemPlan", () => {
  beforeEach(() => {
    __resetProblemStore();
  });

  // 無いと何が静かに通るか: UC-05 のプランは派生物で status を変えない。状態を変えてしまう実装を捕まえる。
  it("プランを追加するが status は open のまま", async () => {
    const before = await loadProblem("p-career");
    const after = await createProblemPlan("p-career");
    expect(after?.plans.length).toBe((before?.plans.length ?? 0) + 1);
    expect(after?.status).toBe("open");
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
