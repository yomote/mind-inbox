/**
 * UX プローブの**台本 (シナリオ) と待受予算**。ux-probe.spec.ts から切り出してある。
 *
 * なぜ spec と別ファイルなのか:
 *   1. **台本を単体テストで凍結する**ため (`ux-probe-scenarios.test.ts`)。台本が変わると
 *      採点・レイテンシの時系列が断絶する (`scenario.id` が断絶点の明示に使われている)
 *      ので、id を上げずに文言だけ書き換わる事故を機械で止める。spec は Playwright を
 *      import するので vitest からは読めない
 *   2. **予算式と実際の timeout を同じ定数から作る**ため。以前は待受予算が spec 冒頭の
 *      コメントに手書きで積み上げてあり、往復数やホップの上限を動かしたときに
 *      予算表記だけが古くなりうる (予算が実態より小さいと、**テスト本体の timeout が先に
 *      来て「どのホップで止まったか」が分からない赤**になる — #287 / #293 の再来)
 *
 * シナリオを足すときは:
 *   - **既存シナリオの `userTurns` を書き換えない**。観測面を増やしたいなら別 id で足す
 *     (書き換えるとその日を境にスコアの意味が変わり、トレンドが読めなくなる)
 *   - 台本は **assistant が何を返しても成立する形**で書く。プローブは応答を読んで分岐
 *     できない (固定台本を送るだけ) ので、特定の応答を前提にした発話は台本として壊れる
 *   - golden-path-monitor の job timeout (`.github/workflows/golden-path-monitor.yml`)
 *     に往復ぶんの予算を足す。足さないと job ごと打ち切られ、失敗の通報にも到達しない
 */

export type ProbeScenario = {
  /** 変更時は id を上げる (時系列比較の断絶点を明示するため)。 */
  readonly id: string;
  readonly description: string;
  readonly userTurns: readonly string[];
};

/**
 * 1 往復目から素直に深掘りへ応じる典型パス。**#123 M0 からの継続線**で、
 * ステータスページのレイテンシ トレンドはこのシナリオを基準線にしている。
 *
 * **1 文字も変えないこと** (#435)。観測面を増やしたいなら下のように別 id で足す。
 */
const WORK_OVERWHELM_V1: ProbeScenario = {
  id: "work-overwhelm-v1",
  description:
    "仕事のタスク過多で眠りが浅い相談者。深掘りに応じて『失敗より失望されるのが怖い』と core が出てくる典型パス",
  userTurns: [
    "最近、仕事のことで頭がいっぱいで眠りが浅いんです。タスクが多すぎて、何から手をつければいいか分からなくて",
    "一番気になっているのは、上司に頼まれた企画書です。期待されている気がして、失敗したらどうしようと考えてしまいます",
    "そう言われてみると、失敗そのものより、上司にがっかりされるのが怖いのかもしれません",
    "少し整理できた気がします。まず何から手をつけるのがいいでしょうか",
  ],
};

/**
 * **否定局面**を含む台本 (#435 / ux-rubric 0.2 の U7「仮説の差し出し方」の観測器)。
 *
 * 何を観測できるようにするか:
 *   rubric 0.2 は「ユーザーが否定・訂正した仮説を、後の turn で同じ枠組みのまま
 *   繰り返している」を U7 = 0 (critical) にしたが、`work-overwhelm-v1` の台本には
 *   否定 turn が 1 つも無く、**この 0 は原理的に発火しない**。つまり毎朝の採点で
 *   U7 が 0 にならないことは「押し付けが無い証拠」にならない。押し付けが起きたときに
 *   **検出できる状態を作る**のがこのシナリオの役目で、毎朝 0 を出すことではない
 *   (素直に引き取れば U7 = 2 になる — それが正常)。
 *
 * 台本の設計 (assistant の応答に依存しない書き方):
 *   - 3 往復目で**何を言われていても成立する否定**を置く (「そういうことではなくて」+
 *     自分の言葉で別方向を示す)。特定の仮説文を引用しない — 引用すると、AI がその仮説を
 *     出さなかった朝に台本が壊れる
 *   - 4 往復目で**もう一度ずらす**。訂正を 1 回だけにすると「1 回は引き取ったが枠組みは
 *     戻っている」型の押し付け (0 の 3 番目のアンカー) を観測する turn が残らない
 *   - 否定のあとに assistant の応答が 2 つ残る (3・4 往復目の応答) ので、
 *     「訂正を引き取って枠組みを変えたか」を採点する材料が必ず記録に入る
 *   - 4 往復目に次の一歩を尋ねる文を兼ねさせている (U4 の終盤・具体化を、
 *     往復数を増やさずに `work-overwhelm-v1` と同じ条件で採点できるようにするため)
 */
const HYPOTHESIS_PUSHBACK_V1: ProbeScenario = {
  id: "hypothesis-pushback-v1",
  description:
    "曖昧な不調から入り、AI の解釈を 2 度否定して別方向を示す相談者。訂正を引き取って枠組みを変えられるか (ux-rubric 0.2 の U7 — 仮説の押し付け) を観測するための台本",
  userTurns: [
    "最近、休みの日でも気持ちが休まらないんです。理由ははっきりしないんですが、ずっと落ち着かない感じが続いていて",
    "強いて言えば、人と会った日の夜に疲れが残っている気がします。人づきあいが嫌いというわけではないんですけど",
    "いえ、そういうことではなくて。うまく言えないんですが、疲れているというより「自分の時間が戻ってこない」ほうが近い気がします",
    "それも少し違う気がします。誰かのせいにしたいわけではなくて、予定が埋まっていること自体が苦しいんだと思います。この感じとはどう付き合っていけばいいんでしょうか",
  ],
};

/**
 * 毎朝のプローブが回すシナリオ。**1 シナリオ = 1 記録 JSON = 1 採点行** で、
 * 蓄積 (`data/ux-observations`) 側は `scenarioId` で分離して集計する。
 */
export const SCENARIOS: readonly ProbeScenario[] = [WORK_OVERWHELM_V1, HYPOTHESIS_PUSHBACK_V1];

/**
 * ホップごとの待受上限 (ms)。**spec が実際に使う値であり、同時に予算式の入力**。
 * ここを動かすと `probeTestTimeoutMs()` も自動で追随する (どちらか片方が古くなれない)。
 */
export const HOP_TIMEOUTS = {
  /**
   * 送信操作 (入力欄への fill / 「送信」クリック) の上限。
   *
   * **上限が無いと、止まった場所と違う場所の名前で赤くなる** — #287 / #293 の実害。
   * live config は `actionTimeout` を持っていなかったため Playwright のアクションは
   * 無期限に actionable 待ちができ、`fill` が止まっても `fill` は落ちない。代わりに
   * **送信前に張ってあった** `waitForResponse` が 210 秒で落ち、
   * 「SSE `/api/chat/stream` が 1 往復目でハングする」として 2 日間追われた。
   * 実際にはリクエストは 1 本も出ていない (#293 のタイトルが指す症状は起きていない)。
   *
   * ここを有限にすると、止まったときに Playwright 自身の call log
   * (`waiting for element to be visible, enabled and editable`) が **job ログに出る**。
   * trace は ADR 0045 で暗号化されており agent は復号できないので、
   * 「入力欄のどの状態が満たされなかったか」を平文のログ側に出せるかが調査可否を分ける。
   */
  sendActionMs: 30_000,
  /**
   * ストリーム応答ヘッダ (最初のバイト) までの上限。1 往復目だけ Container Apps の
   * scale-to-zero コールドスタート (ADR 0013) を見込んで長く取る。
   */
  streamColdMs: 210_000,
  streamWarmMs: 120_000,
  /**
   * ストリーム開始後、応答が出そろう (キャレットが消える) まで待つ上限。
   * 超えた場合は途中経過を warning つきで記録する。
   */
  settleMs: 60_000,
  /** 確定した応答文が DOM で見えることの確認 (expect の上限)。 */
  replyVisibleMs: 30_000,
  /** TTS 応答までの上限。欠測は warn (fail は結線カナリアの仕事)。 */
  ttsColdMs: 120_000,
  ttsWarmMs: 60_000,
  /**
   * 到達 (オンボーディング → サインイン → 新しい相談を始める) の枠。
   *
   * この段の個々の expect の上限を単純に足すと 120s を超えるが、実測は十数秒で、
   * 全部が同時に上限へ張り付く経路は無い。#123 M0 から使ってきた予算表記
   * (「到達+opener 120s」) をそのまま引き継いでいる — **この 1 行だけは
   * 積み上げではなく経験値**であることを明示しておく。
   */
  reachMs: 120_000,
  /** 予算に対する余裕 (従来の 1,590s → setTimeout 1,700s と同じ幅)。 */
  marginMs: 110_000,
} as const;

/**
 * シナリオ 1 本を流し切るのに要る `test.setTimeout` の値 (ms) を返す (純粋関数)。
 *
 * config 既定の 240s では 1 往復も入らないので、シナリオごとにここで計算した値を使う。
 * **小さすぎると、テスト本体の timeout が個々のホップより先に来て「どこで止まったか」が
 * 分からない赤になる**。大きすぎる分には害は無い (壊れているときに気づくのが遅れるだけ)
 * が、job timeout (golden-path-monitor.yml) の積み上げと整合させること。
 */
export function probeTestTimeoutMs(turnCount: number): number {
  const t = HOP_TIMEOUTS;
  const send = t.sendActionMs * 2; // fill + 「送信」クリック
  const coldTurn = send + t.streamColdMs + t.settleMs + t.replyVisibleMs + t.ttsColdMs;
  const warmTurn = send + t.streamWarmMs + t.settleMs + t.replyVisibleMs + t.ttsWarmMs;
  const turns = Math.max(turnCount, 0);
  const body = turns === 0 ? 0 : coldTurn + warmTurn * (turns - 1);
  return t.reachMs + body + t.marginMs;
}
