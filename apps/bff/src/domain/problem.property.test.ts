/**
 * [単体] Problem 集約ルール (追記 / 統合 / 派生フィールド) のプロパティテスト (#259)。
 * 仕様: docs/design/domain_model.md §2.2 / §4.2 / §6 と docs/design/domain_rules.md §3。
 *
 * 無いと何が静かに通るか: mentionCount / lastMentionedAt は派生フィールドなので、
 * 更新漏れやズレがあっても例外は出ず、詳細画面の「言及 N 回」とタイムラインの件数が
 * 食い違ったまま出荷される (= 単体テストの入場条件「静かに間違う」ど真ん中)。
 * L2 (router.test.ts) は代表例で見るが、Problem の形 (status / 件数 / 日時) の
 * 組み合わせはここで性質として固定する。
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { THEMES, type Mention, type Problem } from "../trpc/domain";
import { appendMention, mergeProblems, problemFromMention, withDerived } from "./problem";

// ---- arbitraries -----------------------------------------------------------

const isoDate = (opts: { min: string; max: string }) =>
  fc
    .date({ min: new Date(opts.min), max: new Date(opts.max), noInvalidDate: true })
    .map((d) => d.toISOString());

const arbAffect = fc.record({
  label: fc.constantFrom("不安", "焦り", "安堵"),
  valence: fc.constantFrom("negative", "neutral", "positive" as const),
  intensity: fc.double({ min: 0, max: 1, noNaN: true }),
});

const arbMention = (createdAt: fc.Arbitrary<string>): fc.Arbitrary<Mention> =>
  fc.record({
    id: fc.uuid(),
    sessionId: fc.uuid(),
    dumpId: fc.option(fc.uuid(), { nil: null }),
    createdAt,
    statement: fc.string({ maxLength: 40 }),
    excerpt: fc.string({ maxLength: 40 }),
    affect: arbAffect,
    proposedTheme: fc.constantFrom(...THEMES),
    proposedTags: fc.array(fc.constantFrom("転職", "睡眠", "家計"), { maxLength: 3 }),
    problemId: fc.option(fc.uuid(), { nil: null }),
    groupingConfidence: fc.option(fc.double({ min: 0, max: 1, noNaN: true }), { nil: null }),
  });

/** 既存 Problem。mentionCount は生成後に mentions から整合させる (不変条件を満たす入力)。 */
const arbProblem = (mentionCreatedAt: fc.Arbitrary<string>): fc.Arbitrary<Problem> =>
  fc
    .record({
      id: fc.uuid(),
      title: fc.string({ minLength: 1, maxLength: 26 }),
      summary: fc.string({ maxLength: 60 }),
      theme: fc.constantFrom(...THEMES),
      tags: fc.array(fc.constantFrom("転職", "睡眠", "家計", "評価"), { maxLength: 4 }),
      status: fc.constantFrom("open", "resolved", "shelved" as const),
      mentions: fc.array(arbMention(mentionCreatedAt), { minLength: 1, maxLength: 5 }),
      plans: fc.array(
        fc.record({
          title: fc.string({ maxLength: 20 }),
          steps: fc.array(fc.string({ maxLength: 20 }), { maxLength: 3 }),
        }),
        { maxLength: 2 },
      ),
      createdAt: mentionCreatedAt,
      resolvedAt: fc.option(mentionCreatedAt, { nil: null }),
      shelvedAt: fc.option(mentionCreatedAt, { nil: null }),
    })
    .map((p) =>
      withDerived({ ...p, mentionCount: p.mentions.length, lastMentionedAt: p.createdAt }),
    );

const anyIso = isoDate({ min: "2020-01-01", max: "2030-01-01" });

const arbGrouping = fc.record({
  kind: fc.constantFrom("new", "existing" as const),
  problemId: fc.uuid(),
  problemTitle: fc.string({ minLength: 1, maxLength: 26 }),
  problemTheme: fc.constantFrom(...THEMES),
  isRecurrence: fc.boolean(),
  mentionCount: fc.integer({ min: 1, max: 99 }),
  reignited: fc.boolean(),
  groupingConfidence: fc.option(fc.double({ min: 0, max: 1, noNaN: true }), { nil: null }),
});

// ---- properties ------------------------------------------------------------

describe("[単体] appendMention (property)", () => {
  it("mentionCount は常に mentions.length と一致する (ai-agent の申告値に依存しない)", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbMention(anyIso), (existing, mention) => {
        const updated = appendMention(existing, mention);
        expect(updated.mentionCount).toBe(updated.mentions.length);
        expect(updated.mentions.length).toBe(existing.mentions.length + 1);
      }),
    );
  });

  it("追記後の status は必ず open — 棚卸し済みでも再言及で自動再燃し、棚卸し日時は消える (UC-03)", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbMention(anyIso), (existing, mention) => {
        const updated = appendMention(existing, mention);
        expect(updated.status).toBe("open");
        if (existing.status !== "open") {
          expect(updated.resolvedAt).toBeNull();
          expect(updated.shelvedAt).toBeNull();
        }
      }),
    );
  });

  it("既存の Mention を 1 件も失わない (追記専用 / domain_model §6-3)", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbMention(anyIso), (existing, mention) => {
        const ids = appendMention(existing, mention).mentions.map((m) => m.id);
        for (const m of existing.mentions) {
          expect(ids).toContain(m.id);
        }
        expect(ids[ids.length - 1]).toBe(mention.id);
      }),
    );
  });

  // 既知の仕様の穴 (docs/design/domain_rules.md §3 未決): appendMention は
  // lastMentionedAt に「追記した Mention の createdAt」を無条件で採用する。
  // 過去日時の Mention が後から届く (再送・バックフィル) と lastMentionedAt が
  // **逆行**し、休眠判定・並び順が狂う。relink / merge (withDerived) は max を
  // 取るので、導出ルールが経路によって食い違っている。裁定後、統一したら
  // このテストを通常の it に昇格すること。
  it.fails("lastMentionedAt は常に mentions の最大 createdAt — 未決の仕様の穴", () => {
    fc.assert(
      fc.property(
        // 既存は 2028 年以降、追記 Mention は 2021 年以前 → 必ず逆行する
        arbProblem(isoDate({ min: "2028-01-01", max: "2030-01-01" })),
        arbMention(isoDate({ min: "2020-01-01", max: "2021-01-01" })),
        (existing, mention) => {
          const updated = appendMention(existing, mention);
          const maxCreatedAt = updated.mentions.reduce(
            (max, m) => (m.createdAt > max ? m.createdAt : max),
            updated.mentions[0].createdAt,
          );
          expect(updated.lastMentionedAt).toBe(maxCreatedAt);
        },
      ),
    );
  });
});

describe("[単体] problemFromMention (property)", () => {
  it("新規 Problem は open で立ち、mentionCount は grouping の申告値でなく必ず 1", () => {
    fc.assert(
      fc.property(arbMention(anyIso), arbGrouping, (mention, grouping) => {
        const problem = problemFromMention(mention, grouping);
        expect(problem.status).toBe("open");
        expect(problem.mentions).toEqual([mention]);
        expect(problem.mentionCount).toBe(1);
        expect(problem.resolvedAt).toBeNull();
        expect(problem.shelvedAt).toBeNull();
      }),
    );
  });
});

describe("[単体] mergeProblems (property)", () => {
  it("統合は Mention を 1 件も失わず・増やさず、mentionCount が実体と一致する", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbProblem(anyIso), (target, source) => {
        const merged = mergeProblems(target, source);
        expect(merged.mentions.length).toBe(target.mentions.length + source.mentions.length);
        expect(merged.mentionCount).toBe(merged.mentions.length);
        const ids = merged.mentions.map((m) => m.id).sort();
        const expected = [...target.mentions, ...source.mentions].map((m) => m.id).sort();
        expect(ids).toEqual(expected);
      }),
    );
  });

  it("移した Mention の problemId はすべて統合先を指す (迷子の来歴を作らない)", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbProblem(anyIso), (target, source) => {
        const merged = mergeProblems(target, source);
        const sourceIds = new Set(source.mentions.map((m) => m.id));
        for (const m of merged.mentions) {
          if (sourceIds.has(m.id)) expect(m.problemId).toBe(target.id);
        }
      }),
    );
  });

  it("タグは両者の和集合で重複が無い", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), arbProblem(anyIso), (target, source) => {
        const merged = mergeProblems(target, source);
        expect(new Set(merged.tags).size).toBe(merged.tags.length);
        for (const tag of [...target.tags, ...source.tags]) {
          expect(merged.tags).toContain(tag);
        }
      }),
    );
  });
});

describe("[単体] withDerived (property)", () => {
  it("lastMentionedAt は mentions の最大 createdAt、mentionCount は件数に一致する", () => {
    fc.assert(
      fc.property(arbProblem(anyIso), (problem) => {
        const derived = withDerived(problem);
        expect(derived.mentionCount).toBe(problem.mentions.length);
        const maxCreatedAt = problem.mentions.reduce(
          (max, m) => (m.createdAt > max ? m.createdAt : max),
          problem.mentions[0].createdAt,
        );
        expect(derived.lastMentionedAt).toBe(maxCreatedAt);
      }),
    );
  });
});
