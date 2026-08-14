// 型の真実は api/types.ts (Problem 系はさらに BFF の domain.ts)。
// このファイルは mock 実装専業 — 型を export しない (ADR 0004 の「真実」はデータと挙動)。
import type {
  ActionPlan,
  AssistantReply,
  ChatMessage,
  ConsultationSession,
  ExtractionResult,
  Mention,
  Problem,
  ProblemFilter,
  Theme,
  TriageInput,
} from "./api/types";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const nowText = () => new Date().toISOString();
const uid = () => Math.random().toString(36).slice(2, 10);

export async function startNewConsultation(concern: string): Promise<ConsultationSession> {
  await wait(350);

  return {
    id: uid(),
    // タイトルは最初の発話から自動生成する方針。開始時は仮の見出しのみ。
    // 開始時の見出しは BFF の deriveTitle と同じ「相談セッション」(最初の発話で自動リネーム)。
    title: concern || "相談セッション",
    // AI の挨拶 (初手) は出さない (#241 / dialogue-session.mdx §3.1)。
    // 会話は空で始まり、マスコットの待機表現が「聞く準備ができている」ことを示す。
    messages: [],
  };
}

/**
 * 承認要求 (G1 / dialogue-session.mdx §5.9) を mock で決定的に踏むためのトリガ語。
 *
 * real では ai-agent が副作用ツール (`send_reply` 等 / `approval_mode="always_require"`)
 * を選んだときに立つ。mock は BFF も LLM も呼ばないので、この語を含む発話で代用する
 * (mock はデモ兼 fixture — ADR 0004)。ここが無いと mock ビルドでは承認 UI に一度も
 * 到達できず、デモでも単体テストでも承認の画面を確かめられない。
 */
const APPROVAL_TRIGGER = "返信";

/** ai-agent の確認文と同じ形にする (実物を見たときに別物に見えないように)。 */
const APPROVAL_DESCRIPTION = "「send_reply」を実行するには承認が必要です。実行してよろしいですか？";

export async function sendMessage(_sessionId: string, text: string): Promise<AssistantReply> {
  await wait(300);

  if (text.includes(APPROVAL_TRIGGER)) {
    return {
      message: {
        id: uid(),
        role: "assistant",
        text: APPROVAL_DESCRIPTION,
        createdAt: nowText(),
      },
      approval: { id: `appr-${uid()}`, description: APPROVAL_DESCRIPTION },
    };
  }

  const reply =
    text.length > 45
      ? "詳しく話してくれてありがとう。次に、その出来事で特に気持ちが動いた場面を1つ教えてください。"
      : "受け止めました。次に、そのことが日常へどんな影響を与えているか教えてください。";

  return {
    message: {
      id: uid(),
      role: "assistant",
      text: reply,
      createdAt: nowText(),
    },
    approval: null,
  };
}

/** 承認 / 却下の応答 (§5.9)。却下の文面は ai-agent の `_REJECTION_REPLY` に合わせる。 */
export async function respondToApproval(
  _approvalRequestId: string,
  approved: boolean,
): Promise<ChatMessage> {
  await wait(300);

  return {
    id: uid(),
    role: "assistant",
    text: approved
      ? "「send_reply」を実行しました。他にご用件はありますか？"
      : "操作はキャンセルされました。他にご用件はありますか？",
    createdAt: nowText(),
  };
}

// ===========================================================================
// Phase D — Mention / Problem mock（mock 先行: BFF なしで新体験を一周させる）
//
// domain_model.md の 2層モデル（Mention → Problem）を mock で再現する。
// 網羅する状態（D3 完了条件）:
//   - 再出現（複数 Mention を持つ Problem / 🔁N回）
//   - テーマ分布（固定7分類のうち複数テーマ）
//   - 棚卸し済み（resolved / shelved）
//   - 感情の推移（Mention ごとに affect が変わる）
// トリアージ操作は module スコープの store を書き換えて状態が変わるようにする。
//
// TODO(Phase C): real BFF（problem.* / consultation.extract）に差し替える。store は揮発。
// ===========================================================================

const daysAgo = (n: number) => new Date(Date.now() - 1000 * 60 * 60 * 24 * n).toISOString();
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/**
 * ExtractionResult の件数は **実際の書き込み実績を Problem 単位で** 数える。
 *
 * 2 つのズレをここで潰している (BFF #283 と同じ意味論 / ai-agent `extractor.py` に合わせる):
 *
 * 1. **item 数ではなく problemId の異なり数** — 1 セッションで同じ Problem に複数の
 *    Mention が寄ることがあり、item 数だと「既存に追加 2 件」と過大表示になる
 *    (ai-agent も `updated_problem_count=len(updated_ids)` と集合で数える)
 * 2. **申告 (下書きの grouping.kind) ではなく実績** — preview 後・確定前に対象 Problem が
 *    dismiss / merge で消えていると、確定は「既存に追加」ではなく新規作成になる。
 *    申告のまま数えると「実際は新規作成なのに新規 0 / 既存に追加 1」と表示される
 *
 * `commitPreview` は返す items の grouping を実績に正規化するので、ここはそれを数えるだけ。
 */
const countProblems = (items: ExtractionResult["items"], kind: "new" | "existing") =>
  new Set(items.filter((i) => i.grouping.kind === kind).map((i) => i.grouping.problemId)).size;

function makeMention(input: {
  id: string;
  problemId: string;
  sessionId: string;
  daysAgo: number;
  statement: string;
  excerpt: string;
  affect: Mention["affect"];
  theme: Theme;
  tags: string[];
  confidence: number | null;
}): Mention {
  return {
    id: input.id,
    sessionId: input.sessionId,
    dumpId: input.sessionId,
    createdAt: daysAgo(input.daysAgo),
    statement: input.statement,
    excerpt: input.excerpt,
    affect: input.affect,
    proposedTheme: input.theme,
    proposedTags: input.tags,
    problemId: input.problemId,
    groupingConfidence: input.confidence,
  };
}

function deriveProblemDates(problem: Problem): Problem {
  const sorted = [...problem.mentions].sort((a, b) => (a.createdAt < b.createdAt ? -1 : 1));
  return {
    ...problem,
    mentionCount: problem.mentions.length,
    createdAt: sorted[0]?.createdAt ?? problem.createdAt,
    lastMentionedAt: sorted[sorted.length - 1]?.createdAt ?? problem.lastMentionedAt,
  };
}

/**
 * 初期 Problem 群を生成する（store seed 兼 spec プレビュー用の決め打ちデータ）。
 * spec preview からも import して使い、fixture の二重管理を避ける（PR #44 レビュー指摘）。
 */
export function seedProblems(): Problem[] {
  const seeds: Problem[] = [
    // 再出現の主役: 3回言及（🔁3回）。感情が「焦り→不安→少しの整理」と推移する。
    deriveProblemDates({
      id: "p-career",
      title: "転職すべきか迷っている",
      summary:
        "今の職場で成長実感が薄く転職を検討しているが、辞めることへの罪悪感と収入の不安で踏み切れない。",
      theme: "仕事・キャリア",
      tags: ["転職", "成長実感", "罪悪感"],
      status: "open",
      mentions: [
        makeMention({
          id: "m-career-1",
          problemId: "p-career",
          sessionId: "s-1001",
          daysAgo: 28,
          statement: "今の仕事で成長している実感がなく、転職を考え始めた。",
          excerpt: "このままここにいて成長できるのか不安で…",
          affect: { label: "停滞感", valence: "negative", intensity: 0.5 },
          theme: "仕事・キャリア",
          tags: ["転職", "成長実感"],
          confidence: null,
        }),
        makeMention({
          id: "m-career-2",
          problemId: "p-career",
          sessionId: "s-1014",
          daysAgo: 12,
          statement: "辞めたい気持ちはあるが、世話になった人を裏切る罪悪感がある。",
          excerpt: "辞めたら今のチームに申し訳ないって思っちゃう",
          affect: { label: "罪悪感", valence: "negative", intensity: 0.72 },
          theme: "仕事・キャリア",
          tags: ["罪悪感"],
          confidence: 0.86,
        }),
        makeMention({
          id: "m-career-3",
          problemId: "p-career",
          sessionId: "s-1022",
          daysAgo: 3,
          statement: "転職サイトに登録だけしてみた。少し前に進んだ感覚がある。",
          excerpt: "とりあえず登録だけしてみたら気が楽になった",
          affect: { label: "前向き", valence: "positive", intensity: 0.4 },
          theme: "仕事・キャリア",
          tags: ["転職", "行動"],
          confidence: 0.9,
        }),
      ],
      mentionCount: 3,
      plans: [],
      createdAt: daysAgo(28),
      lastMentionedAt: daysAgo(3),
      resolvedAt: null,
      shelvedAt: null,
    }),
    // 再出現: 2回（🔁2回）。心と体テーマ。
    deriveProblemDates({
      id: "p-sleep",
      title: "夜うまく眠れない",
      summary: "考えごとで寝つきが悪く、翌日の集中力が落ちている。睡眠リズムを立て直したい。",
      theme: "心と体",
      tags: ["睡眠", "疲労"],
      status: "open",
      mentions: [
        makeMention({
          id: "m-sleep-1",
          problemId: "p-sleep",
          sessionId: "s-1008",
          daysAgo: 20,
          statement: "夜になると考えごとが止まらず寝つけない。",
          excerpt: "布団に入っても頭が冴えちゃって",
          affect: { label: "焦り", valence: "negative", intensity: 0.6 },
          theme: "心と体",
          tags: ["睡眠"],
          confidence: null,
        }),
        makeMention({
          id: "m-sleep-2",
          problemId: "p-sleep",
          sessionId: "s-1019",
          daysAgo: 6,
          statement: "寝不足が続いて日中ぼーっとする時間が増えた。",
          excerpt: "最近ずっと眠くて仕事に集中できない",
          affect: { label: "疲労", valence: "negative", intensity: 0.68 },
          theme: "心と体",
          tags: ["睡眠", "疲労"],
          confidence: 0.82,
        }),
      ],
      mentionCount: 2,
      plans: [
        {
          title: "睡眠リズム立て直しプラン",
          steps: ["就寝1時間前にスマホを置く", "起床時間を固定する", "朝に5分散歩する"],
        },
      ],
      createdAt: daysAgo(20),
      lastMentionedAt: daysAgo(6),
      resolvedAt: null,
      shelvedAt: null,
    }),
    // 単発（種のみ）。お金テーマ。
    deriveProblemDates({
      id: "p-money",
      title: "将来のお金が漠然と不安",
      summary: "収入は安定しているが、老後や急な出費を考えると将来のお金に漠然とした不安がある。",
      theme: "お金",
      tags: ["将来不安", "貯蓄"],
      status: "open",
      mentions: [
        makeMention({
          id: "m-money-1",
          problemId: "p-money",
          sessionId: "s-1016",
          daysAgo: 9,
          statement: "このままの貯蓄ペースで将来やっていけるのか不安。",
          excerpt: "老後とか考えると急に怖くなる",
          affect: { label: "不安", valence: "negative", intensity: 0.55 },
          theme: "お金",
          tags: ["将来不安"],
          confidence: null,
        }),
      ],
      mentionCount: 1,
      plans: [],
      createdAt: daysAgo(9),
      lastMentionedAt: daysAgo(9),
      resolvedAt: null,
      shelvedAt: null,
    }),
    // 棚卸し済み（shelved）。家族・パートナーテーマ。
    deriveProblemDates({
      id: "p-family",
      title: "親との距離感に疲れる",
      summary: "実家に帰るたび干渉されて疲れる。今は割り切ることにした。",
      theme: "家族・パートナー",
      tags: ["親", "干渉"],
      status: "shelved",
      mentions: [
        makeMention({
          id: "m-family-1",
          problemId: "p-family",
          sessionId: "s-1003",
          daysAgo: 40,
          statement: "実家に帰ると進路に口を出されて疲れる。",
          excerpt: "帰省するたびに色々言われてどっと疲れる",
          affect: { label: "疲労", valence: "negative", intensity: 0.5 },
          theme: "家族・パートナー",
          tags: ["親", "干渉"],
          confidence: null,
        }),
        makeMention({
          id: "m-family-2",
          problemId: "p-family",
          sessionId: "s-1011",
          daysAgo: 24,
          statement: "親は変わらないので、距離を取って割り切ることにした。",
          excerpt: "もう期待するのはやめようと思った",
          affect: { label: "諦め", valence: "neutral", intensity: 0.3 },
          theme: "家族・パートナー",
          tags: ["割り切り"],
          confidence: 0.78,
        }),
      ],
      mentionCount: 2,
      plans: [],
      createdAt: daysAgo(40),
      lastMentionedAt: daysAgo(24),
      resolvedAt: null,
      shelvedAt: daysAgo(20),
    }),
    // 解決済み（resolved）。日常・生活テーマ。
    deriveProblemDates({
      id: "p-habit",
      title: "運動習慣が続かない",
      summary: "運動が三日坊主だったが、朝の散歩を習慣化できて解決した。",
      theme: "日常・生活",
      tags: ["運動", "習慣"],
      status: "resolved",
      mentions: [
        makeMention({
          id: "m-habit-1",
          problemId: "p-habit",
          sessionId: "s-1006",
          daysAgo: 33,
          statement: "運動しようと思っても三日坊主で続かない。",
          excerpt: "何度やっても続かなくて自己嫌悪",
          affect: { label: "自己嫌悪", valence: "negative", intensity: 0.45 },
          theme: "日常・生活",
          tags: ["運動", "習慣"],
          confidence: null,
        }),
      ],
      mentionCount: 1,
      plans: [],
      createdAt: daysAgo(33),
      lastMentionedAt: daysAgo(33),
      resolvedAt: daysAgo(15),
      shelvedAt: null,
    }),
    // 直近の新規（人間関係テーマ）。
    deriveProblemDates({
      id: "p-relations",
      title: "新しい職場の人間関係",
      summary: "異動先のチームにまだ馴染めず、雑談の輪に入りづらい。",
      theme: "人間関係",
      tags: ["職場", "馴染めない"],
      status: "open",
      mentions: [
        makeMention({
          id: "m-relations-1",
          problemId: "p-relations",
          sessionId: "s-1024",
          daysAgo: 1,
          statement: "異動先のチームにまだ馴染めず居心地が悪い。",
          excerpt: "雑談に入っていけなくて浮いてる気がする",
          affect: { label: "孤立感", valence: "negative", intensity: 0.5 },
          theme: "人間関係",
          tags: ["職場", "馴染めない"],
          confidence: null,
        }),
      ],
      mentionCount: 1,
      plans: [],
      createdAt: daysAgo(1),
      lastMentionedAt: daysAgo(1),
      resolvedAt: null,
      shelvedAt: null,
    }),
  ];

  return seeds;
}

// module スコープの揮発 store。トリアージ / 抽出で書き換わる。
let problemStore: Problem[] = seedProblems();

/** テスト / Storybook 用に store を初期状態へ戻す。 */
export function __resetProblemStore(): void {
  problemStore = seedProblems();
}

function getProblem(id: string): Problem | undefined {
  return problemStore.find((p) => p.id === id);
}

/**
 * 段1 抽出 + 段2 グルーピング（mock）。
 * 1セッション全文（sessionId）→ 2件の Mention を抽出し、
 *   - 1件は既存 Problem「転職」へ寄せる（🔁 再出現）
 *   - 1件は新規 Problem を起こす（🆕）
 * store を書き換えるので、続く一覧 / 詳細に反映される。
 */
export async function extractMentions(sessionId: string): Promise<ExtractionResult> {
  await wait(500);

  // 既存「転職」Problem へ寄せる再出現。
  const career = getProblem("p-career");
  const recurrenceMention = makeMention({
    id: `m-${uid()}`,
    problemId: "p-career",
    sessionId,
    daysAgo: 0,
    statement: "やっぱり転職のことが頭から離れない。面談だけでも受けてみようか迷う。",
    excerpt: "面談だけでも受けてみようかな…でも怖い",
    affect: { label: "迷い", valence: "negative", intensity: 0.58 },
    theme: "仕事・キャリア",
    tags: ["転職", "迷い"],
    confidence: 0.88,
  });

  let recurrenceCount = 1;
  if (career) {
    career.mentions.push(recurrenceMention);
    const derived = deriveProblemDates({ ...career, status: "open" });
    Object.assign(career, derived);
    recurrenceCount = career.mentionCount;
  }

  // 新規 Problem を起こす。
  const newProblemId = `p-${uid()}`;
  const newMention = makeMention({
    id: `m-${uid()}`,
    problemId: newProblemId,
    sessionId,
    daysAgo: 0,
    statement: "来週のプレゼンが不安で気が重い。",
    excerpt: "人前で話すの本当に苦手で今から憂鬱",
    affect: { label: "緊張", valence: "negative", intensity: 0.65 },
    theme: "仕事・キャリア",
    tags: ["プレゼン", "緊張"],
    confidence: null,
  });
  const newProblem = deriveProblemDates({
    id: newProblemId,
    title: "来週のプレゼンが不安",
    summary: "人前で話すのが苦手で、来週のプレゼンを前に気が重い。",
    theme: "仕事・キャリア",
    tags: ["プレゼン", "緊張"],
    status: "open",
    mentions: [newMention],
    mentionCount: 1,
    plans: [],
    createdAt: daysAgo(0),
    lastMentionedAt: daysAgo(0),
    resolvedAt: null,
    shelvedAt: null,
  });
  problemStore = [newProblem, ...problemStore];

  return {
    sessionId,
    items: [
      {
        mention: clone(newMention),
        grouping: {
          kind: "new",
          problemId: newProblemId,
          problemTitle: newProblem.title,
          problemTheme: newProblem.theme,
          isRecurrence: false,
          mentionCount: 1,
          reignited: false,
          groupingConfidence: null,
        },
      },
      {
        mention: clone(recurrenceMention),
        grouping: {
          kind: "existing",
          problemId: "p-career",
          problemTitle: career?.title ?? "転職すべきか迷っている",
          problemTheme: "仕事・キャリア",
          isRecurrence: true,
          mentionCount: recurrenceCount,
          reignited: false,
          groupingConfidence: recurrenceMention.groupingConfidence,
        },
      },
    ],
    newProblemCount: 1,
    updatedProblemCount: career ? 1 : 0,
  };
}

/**
 * 読み取り専用の抽出プレビュー (#187 / ADR 0039 D1)。
 *
 * **store には一切書かない** — 右ペインの下書きは揮発し、確定 (extractMentions) して
 * 初めて Problem が増える。ここが書いてしまうと「確定していない下書き」が
 * 蓄積データを静かに汚す (ADR 0039 の核を壊す)。
 *
 * 会話が進むほど下書きが「増える / 育つ」体験を mock で再現する:
 *   - ユーザー発話 1 件〜: 既存「転職」への再出現 (🔁) の下書き
 *   - ユーザー発話 3 件〜: 新規 Problem (プレゼン不安) の下書きが加わる
 * excerpt には実際のユーザー発話を引用し、「話した内容が形になっていく」を見せる。
 */
export async function previewExtraction(
  sessionId: string,
  messages: ChatMessage[],
): Promise<ExtractionResult> {
  await wait(600);

  const userTexts = messages.filter((m) => m.role === "user").map((m) => m.text);
  const items: ExtractionResult["items"] = [];

  if (userTexts.length >= 1) {
    // 既存 Problem への再出現の下書き。mentionCount は「確定したらこうなる」件数。
    const career = getProblem("p-career");
    items.push({
      mention: makeMention({
        id: `m-draft-${sessionId}-recur`,
        problemId: "p-career",
        sessionId,
        daysAgo: 0,
        statement: "やっぱり転職のことが頭から離れない。面談だけでも受けてみようか迷う。",
        excerpt: userTexts[0].slice(0, 60),
        affect: { label: "迷い", valence: "negative", intensity: 0.58 },
        theme: "仕事・キャリア",
        tags: ["転職", "迷い"],
        confidence: 0.88,
      }),
      grouping: {
        kind: "existing",
        problemId: "p-career",
        problemTitle: career?.title ?? "転職すべきか迷っている",
        problemTheme: "仕事・キャリア",
        isRecurrence: true,
        mentionCount: (career?.mentionCount ?? 3) + 1,
        reignited: false,
        groupingConfidence: 0.88,
      },
    });
  }

  if (userTexts.length >= 3) {
    // 新規 Problem の下書き。id は draft 前置で「未確定」を機械的に区別できるようにする。
    const draftProblemId = `p-draft-${sessionId}`;
    items.push({
      mention: makeMention({
        id: `m-draft-${sessionId}-new`,
        problemId: draftProblemId,
        sessionId,
        daysAgo: 0,
        statement: "来週のプレゼンが不安で気が重い。",
        excerpt: userTexts[2].slice(0, 60),
        affect: { label: "緊張", valence: "negative", intensity: 0.65 },
        theme: "仕事・キャリア",
        tags: ["プレゼン", "緊張"],
        confidence: null,
      }),
      grouping: {
        kind: "new",
        problemId: draftProblemId,
        problemTitle: "来週のプレゼンが不安",
        problemTheme: "仕事・キャリア",
        isRecurrence: false,
        mentionCount: 1,
        reignited: false,
        groupingConfidence: null,
      },
    });
  }

  return {
    sessionId,
    items,
    newProblemCount: countProblems(items, "new"),
    updatedProblemCount: countProblems(items, "existing"),
  };
}

/**
 * 表示中の下書きをそのまま確定する (#187 / ADR 0039 D1・D3 — 「この内容で確定」)。
 *
 * **再抽出しない**: 抽出は非決定的なので、確定時に抽出し直すと画面で確認した内容と
 * 違うものが保存されうる (PR #282 Codex P1)。入力の drafts (previewExtraction が返した
 * items) を**そのまま** Problem store へ書き、保存された結果を ExtractionResult として返す。
 *
 * mock は真実 (ADR 0004) — この入出力が BFF 側 commit 経路 (#283) の契約の写しになる:
 *   入力: sessionId + drafts (ExtractedItem[] — zod は ExtractedItemSchema がそのまま使える)
 *   出力: ExtractionResult (id 類はサーバー権威で採番し直してよい。mock は draft id を流用)
 */
export async function commitPreview(
  sessionId: string,
  drafts: ExtractionResult["items"],
): Promise<ExtractionResult> {
  await wait(400);

  // このバッチで新しく起こした Problem。あとから同じ Problem へ寄る Mention が来ても
  // 「既存に追加」ではなく新規の一部として数えるために覚えておく (BFF #283 と同じ) —
  // でないと 1 つの Problem が「新規 1 件」かつ「既存に追加 1 件」に二重計上される。
  const createdHere = new Set<string>();

  const items: ExtractionResult["items"] = drafts.map(({ mention, grouping }) => {
    // kind を問わず problemId で引く (BFF の materialize と同じ意味論): 同じ id を持つ
    // 下書きが複数あっても Problem は 1 つで、2 件目以降は Mention の追記になる。
    // ここが id を見ずに新規を作ると、distinct 件数 (countProblems) と実態がズレる。
    const existing = getProblem(grouping.problemId);
    if (existing) {
      // 既存 Problem への再出現: 下書きの Mention をそのまま追記する。
      existing.mentions.push({ ...clone(mention), problemId: existing.id });
      Object.assign(existing, deriveProblemDates({ ...existing, status: "open" }));
      return {
        mention: clone(mention),
        grouping: {
          ...clone(grouping),
          // 実績に正規化する: このバッチで起こした Problem への追記は "new" のまま。
          kind: createdHere.has(existing.id) || grouping.kind === "new" ? "new" : "existing",
          mentionCount: existing.mentionCount,
        },
      };
    }

    // 新規、**または確定までの間に既存が消えていた場合** (preview 後に dismiss / merge された)。
    // 取りこぼさず新規として作り、実績も "new" に正規化する — 申告 (grouping.kind) のまま
    // 数えると「実際は新規作成なのに『既存に追加 1 件』」とレビュー画面に出る (BFF #283)。
    const problem = deriveProblemDates({
      id: grouping.problemId,
      title: grouping.problemTitle,
      summary: mention.statement,
      theme: grouping.problemTheme,
      tags: [...mention.proposedTags],
      status: "open",
      mentions: [{ ...clone(mention), problemId: grouping.problemId }],
      mentionCount: 1,
      plans: [],
      createdAt: mention.createdAt,
      lastMentionedAt: mention.createdAt,
      resolvedAt: null,
      shelvedAt: null,
    });
    problemStore = [problem, ...problemStore];
    createdHere.add(problem.id);
    return {
      mention: clone(mention),
      grouping: { ...clone(grouping), kind: "new", mentionCount: 1, isRecurrence: false },
    };
  });

  // 件数は**実際の書き込み実績**から数える (正規化済みの items がその実績)。
  return {
    sessionId,
    items,
    newProblemCount: countProblems(items, "new"),
    updatedProblemCount: countProblems(items, "existing"),
  };
}

/** UC-02 一覧。既定は直近言及順。store のスナップショット（複製）を返す。 */
export async function loadProblems(filter?: ProblemFilter): Promise<Problem[]> {
  await wait(250);

  let items = [...problemStore];
  if (filter?.theme) {
    items = items.filter((p) => p.theme === filter.theme);
  }
  if (filter?.status) {
    items = items.filter((p) => p.status === filter.status);
  }
  items.sort((a, b) => (a.lastMentionedAt < b.lastMentionedAt ? 1 : -1));
  return clone(items);
}

export async function loadProblem(id: string): Promise<Problem | null> {
  await wait(200);
  const found = getProblem(id);
  return found ? clone(found) : null;
}

/**
 * 事後トリアージ（mock）。store を書き換えて更新後の Problem を返す。
 * dismiss は store から取り除くため null を返す。
 */
export async function triageProblem(input: TriageInput): Promise<Problem | null> {
  await wait(250);

  const problem = getProblem(input.problemId);
  if (!problem) return null;

  switch (input.action) {
    case "resolve":
      problem.status = "resolved";
      problem.resolvedAt = nowText();
      problem.shelvedAt = null;
      break;
    case "shelve":
      problem.status = "shelved";
      problem.shelvedAt = nowText();
      problem.resolvedAt = null;
      break;
    case "reopen":
      problem.status = "open";
      problem.resolvedAt = null;
      problem.shelvedAt = null;
      break;
    case "editTheme":
      if (input.theme) problem.theme = input.theme;
      break;
    case "editTitle":
      if (input.title && input.title.trim()) problem.title = input.title.trim();
      break;
    case "dismiss":
      problemStore = problemStore.filter((p) => p.id !== input.problemId);
      return null;
    case "relink": {
      // Mention を別 Problem へ移す（再リンク）。Mention は不変なので新インスタンスへ複製。
      const target = input.targetProblemId ? getProblem(input.targetProblemId) : undefined;
      const moving = problem.mentions.find((m) => m.id === input.mentionId);
      if (target && moving && problem.mentions.length > 1) {
        problem.mentions = problem.mentions.filter((m) => m.id !== input.mentionId);
        target.mentions.push({ ...moving, problemId: target.id });
        Object.assign(problem, deriveProblemDates(problem));
        Object.assign(target, deriveProblemDates(target));
      }
      break;
    }
    case "merge": {
      // 別 Problem を本 Problem に統合（mention を吸い上げて吸収元を削除）。
      const source = input.targetProblemId ? getProblem(input.targetProblemId) : undefined;
      if (source && source.id !== problem.id) {
        problem.mentions.push(...source.mentions.map((m) => ({ ...m, problemId: problem.id })));
        problem.plans.push(...source.plans);
        Object.assign(problem, deriveProblemDates(problem));
        problemStore = problemStore.filter((p) => p.id !== source.id);
      }
      break;
    }
  }

  return clone(problem);
}

/**
 * UC-05: Problem の文脈から次の一歩（ActionPlan）を生成して紐づける（mock）。
 * 状態は変えない（plans は派生物）。更新後の Problem を返す。
 */
export async function createProblemPlan(problemId: string): Promise<Problem | null> {
  await wait(400);

  const problem = getProblem(problemId);
  if (!problem) return null;

  const plan: ActionPlan = {
    title: `「${problem.title}」への小さな一歩`,
    steps: [
      "今日できる15分のアクションを1つ決める",
      "気持ちが動いたら、その場で書き留める",
      "1週間後に状況を見返す",
    ],
  };
  problem.plans.push(plan);

  return clone(problem);
}
