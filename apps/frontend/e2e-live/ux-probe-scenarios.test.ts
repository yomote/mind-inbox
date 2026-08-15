import { describe, expect, it } from "vitest";
import { HOP_TIMEOUTS, probeTestTimeoutMs, SCENARIOS } from "./ux-probe-scenarios";

/**
 * [単体] UX プローブの台本と待受予算 (#123 M0 / #435)。
 *
 * 台本は実環境でしか流せない (L4) が、**台本そのものの不変条件は単体で押さえられる**。
 * ここが無いと静かに通るもの:
 *   - 継続線 `work-overwhelm-v1` の文言が書き換わり、`scenario.id` は同じまま
 *     スコア・レイテンシの意味だけが変わる (トレンドが断絶しているのに断絶が見えない)
 *   - 否定局面シナリオの否定 turn が末尾へ移り、「訂正を引き取ったか」を採点する
 *     assistant 応答が記録に残らないまま「U7 の観測器がある」ことになる (#435 の目的が消える)
 *   - 待受予算がホップ上限の合計を下回り、テスト本体の timeout が先に来て
 *     **どのホップで止まったかが分からない赤**になる (#287 / #293 の再来)
 */

const byId = (id: string) => SCENARIOS.find((s) => s.id === id);

describe("[単体] UX プローブの台本", () => {
  it("シナリオ id は一意 (記録・採点の分離キーなので衝突すると集計が混ざる)", () => {
    const ids = SCENARIOS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("継続線 work-overwhelm-v1 の台本は凍結されている (変えるなら id を上げる)", () => {
    // **この配列は #123 M0 から積み上げてきたトレンドの前提**。文言を変えるということは
    // 別のシナリオを同じ名前で流すということで、過去の点と比べられなくなる。
    // 変更が要るなら id を上げて別シナリオとして足し、この期待値は残す。
    expect(byId("work-overwhelm-v1")?.userTurns).toEqual([
      "最近、仕事のことで頭がいっぱいで眠りが浅いんです。タスクが多すぎて、何から手をつければいいか分からなくて",
      "一番気になっているのは、上司に頼まれた企画書です。期待されている気がして、失敗したらどうしようと考えてしまいます",
      "そう言われてみると、失敗そのものより、上司にがっかりされるのが怖いのかもしれません",
      "少し整理できた気がします。まず何から手をつけるのがいいでしょうか",
    ]);
  });

  it("hypothesis-pushback-v1 は否定 turn を持ち、その後に応答が 2 往復ぶん残る", () => {
    const scenario = byId("hypothesis-pushback-v1");
    expect(scenario).toBeDefined();
    const turns = scenario!.userTurns;

    // 否定 = 「AI が何を言っていても成立する」形の打ち消し。台本が肯定方向だけに
    // 書き換わると U7 の 0 (押し付け) は原理的に発火しなくなる
    const negationIndex = turns.findIndex(
      (t) => t.includes("そういうことではなくて") || t.includes("違う"),
    );
    expect(negationIndex).toBeGreaterThanOrEqual(0);

    // 否定より後に user 発話が 1 つ以上 = 否定後の assistant 応答が 2 つ記録に残る
    // (否定 turn への応答 + その次の応答)。ここが 0 だと「訂正を引き取って枠組みを
    // 変えたか / 元の仮説へ引き戻したか」を採点する材料が記録に入らない
    expect(turns.length - 1 - negationIndex).toBeGreaterThanOrEqual(1);
  });

  it("台本は 1 往復以上ある (空の台本は記録 0 件を毎朝積むだけになる)", () => {
    for (const scenario of SCENARIOS) {
      expect(scenario.userTurns.length).toBeGreaterThan(0);
      expect(scenario.description.trim()).not.toBe("");
    }
  });
});

describe("[単体] 待受予算", () => {
  it("4 往復の予算は従来値 1,700,000ms のまま (継続線の実行条件を変えない)", () => {
    // 定数の切り出し (#435) で予算が静かに縮むと、work-overwhelm-v1 が
    // 途中で打ち切られる朝が出る。従来の手書き予算と同じ値であることを固定する
    expect(probeTestTimeoutMs(4)).toBe(1_700_000);
  });

  it("1 往復ぶんのホップ上限の合計を必ず上回る", () => {
    const t = HOP_TIMEOUTS;
    const oneWarmTurn =
      t.sendActionMs * 2 + t.streamWarmMs + t.settleMs + t.replyVisibleMs + t.ttsWarmMs;
    for (const scenario of SCENARIOS) {
      const turns = scenario.userTurns.length;
      // 予算 > 到達 + (往復数 × 温まった往復の上限) — コールドスタート枠と余裕のぶん
      // 必ず上回る。下回ると「どのホップで止まったか」が読めない赤になる
      expect(probeTestTimeoutMs(turns)).toBeGreaterThan(t.reachMs + oneWarmTurn * turns);
      // playwright.live.config.ts の既定 (240s) では 1 往復も入らない
      expect(probeTestTimeoutMs(turns)).toBeGreaterThan(240_000);
    }
  });

  it("往復数を増やすと予算も増える", () => {
    expect(probeTestTimeoutMs(5)).toBeGreaterThan(probeTestTimeoutMs(4));
    expect(probeTestTimeoutMs(0)).toBe(HOP_TIMEOUTS.reachMs + HOP_TIMEOUTS.marginMs);
  });
});
