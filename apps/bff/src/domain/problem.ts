/**
 * Problem 集約の純粋ドメインルール (#259 / testing strategy「単体テストの入場条件」)。
 *
 * ここにあるのは「壊れても例外が出ず、データが静かに間違う」導出・併合・状態遷移の
 * ルールだけ。リポジトリ I/O・tRPC のエラー変換は router 側 (`../trpc/router.ts`) が持つ。
 * 型の真実は `../trpc/domain.ts` (zod)。仕様の明文化は docs/design/domain_rules.md。
 */

import type { GroupingOutcome, Mention, Problem, StoredMention } from "../trpc/domain";

export function dedupe(values: string[]): string[] {
  return [...new Set(values)];
}

/** mentions 変更後に派生フィールド（mentionCount / lastMentionedAt）を再計算する。 */
export function withDerived(problem: Problem): Problem {
  const mentionCount = problem.mentions.length;
  const lastMentionedAt = problem.mentions.reduce(
    (max, m) => (isLater(m.createdAt, max) ? m.createdAt : max),
    problem.mentions[0]?.createdAt ?? problem.lastMentionedAt,
  );
  return { ...problem, mentionCount, lastMentionedAt };
}

/**
 * ISO 8601 文字列を時刻として比較する（a が b より後なら true）。
 * 辞書順比較はタイムゾーンオフセット混在（"+09:00" と "Z" 等）で時系列と
 * 食い違うため、epoch へ正規化して比べる（PR #274 Codex 指摘）。
 * どちらかが時刻として読めない文字列のときだけ、従来の辞書順に落とす
 * （壊れた値で NaN 比較が常に false になり「最初の値が勝ち続ける」のを防ぐ）。
 */
function isLater(a: string, b: string): boolean {
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return a > b;
  return ta > tb;
}

/**
 * 既存 Problem に再言及 (新しい Mention) を追記する。
 *
 * - 言及回数は mentions から導出する。ai-agent の申告値をそのまま持つと、
 *   候補集合のズレや再送で実体（mentions.length）と食い違う。
 * - 棚卸し済み（resolved / shelved）への再言及は **`open` に戻す**（UC-03 の事後条件 /
 *   domain_model.md §4.2「再燃は自動」）。棚卸し日時も消す (再オープンなのに
 *   解決日時が残っていると履歴が嘘になる)。
 * - `lastMentionedAt` は **常に全 Mention の `createdAt` の最大値** (2026-08-11 PO 裁定 /
 *   docs/design/domain_rules.md §3)。追記 Mention の日時を無条件採用すると、
 *   過去日時の Mention が後から届いたとき (再送・バックフィル・時計ずれ) に逆行して
 *   休眠判定・並び順が狂う。relink / merge と同じく `withDerived` に委譲して統一する。
 * - 再オープンさせた Mention には来歴 `reopenedProblem: true` を残す (#283)。追記後は必ず
 *   `open` なので「どの Mention が戻したか」は状態から再構成できず、同じ下書きの再送で
 *   確定応答の「再燃」表示だけが消えてしまう。
 */
export function appendMention(existing: Problem, mention: Mention): Problem {
  // 再燃は「**この** Mention が棚卸し済みを open に戻した」という一回きりの事実。追記後の
  // Problem (status: open / Mention 保存済み) からは再構成できないので、Mention 自身に
  // 来歴として残す — 同じ下書きの再送でも確定応答を同じにするため (#283)。
  const stored: StoredMention =
    existing.status === "open" ? mention : { ...mention, reopenedProblem: true };
  return withDerived({
    ...existing,
    mentions: [...existing.mentions, stored],
    ...(existing.status === "open"
      ? {}
      : { status: "open" as const, resolvedAt: null, shelvedAt: null }),
  });
}

/**
 * 抽出結果から新規 Problem を起こす（grouping.kind === "new"、または existing だが
 * 候補が見つからないフォールバック）。
 * Mention は 1 件なので mentionCount は必ず 1（mentions.length と一致させる。
 * grouping.mentionCount は既存追記前提の値なのでここでは使わない）。
 */
export function problemFromMention(mention: Mention, grouping: GroupingOutcome): Problem {
  return {
    id: grouping.problemId,
    title: grouping.problemTitle,
    summary: mention.statement,
    theme: grouping.problemTheme,
    tags: mention.proposedTags,
    status: "open",
    mentions: [mention],
    mentionCount: 1,
    plans: [],
    createdAt: mention.createdAt,
    lastMentionedAt: mention.createdAt,
    resolvedAt: null,
    shelvedAt: null,
  };
}

/**
 * source を target に統合した Problem を返す（トリアージ merge の純粋部分）。
 * Mention は取りこぼさず移し (problemId を付け替え)、tags は重複除去、plans は連結。
 * source の削除 (リポジトリ操作) は呼び出し側の責務。
 */
export function mergeProblems(target: Problem, source: Problem): Problem {
  const movedMentions = source.mentions.map((m) => ({ ...m, problemId: target.id }));
  return withDerived({
    ...target,
    mentions: [...target.mentions, ...movedMentions],
    tags: dedupe([...target.tags, ...source.tags]),
    plans: [...target.plans, ...source.plans],
  });
}
