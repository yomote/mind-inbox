import type {
  ExtractionResult,
  Mention,
  Problem,
  ProblemStatus,
  Theme,
  TriageAction,
} from "../../bff/src/trpc/domain";

export type {
  Affect,
  ExtractedItem,
  ExtractionResult,
  GroupingOutcome,
  Mention,
  Problem,
  ProblemStatus,
  Theme,
  TriageAction,
} from "../../bff/src/trpc/domain";

export type Screen =
  | "onboarding"
  | "home"
  | "specPreview"
  | "newConsultation"
  | "session"
  | "result"
  | "actionPlan"
  | "history"
  | "settings"
  | "paused"
  | "crisisSupport"
  | "extractReview"
  | "problemList"
  | "problemDetail";

export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  createdAt: string;
};

export type ConsultationSession = {
  id: string;
  title: string;
  messages: ChatMessage[];
};

export type OrganizedResult = {
  summary: string;
  emotions: string[];
  priorities: string[];
};

export type ActionPlan = {
  title: string;
  steps: string[];
};

export type HistoryItem = {
  id: string;
  title: string;
  createdAt: string;
  result: OrganizedResult;
  plan: ActionPlan;
};

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const nowText = () => new Date().toISOString();
const uid = () => Math.random().toString(36).slice(2, 10);

export async function startNewConsultation(concern: string): Promise<ConsultationSession> {
  await wait(350);

  return {
    id: uid(),
    // タイトルは最初の発話から自動生成する方針。開始時は仮の見出しのみ。
    title: concern || "新しい相談",
    messages: [
      {
        id: uid(),
        role: "assistant",
        text: `ありがとうございます。\nまずは「今いちばん気になっていること」を1つ教えてください。`,
        createdAt: nowText(),
      },
    ],
  };
}

export async function sendMessage(_sessionId: string, text: string): Promise<ChatMessage> {
  await wait(300);

  const reply =
    text.length > 45
      ? "詳しく話してくれてありがとう。次に、その出来事で特に気持ちが動いた場面を1つ教えてください。"
      : "受け止めました。次に、そのことが日常へどんな影響を与えているか教えてください。";

  return {
    id: uid(),
    role: "assistant",
    text: reply,
    createdAt: nowText(),
  };
}

export async function organizeResult(_sessionId: string): Promise<OrganizedResult> {
  await wait(450);

  return {
    summary: "現在のテーマには不安と疲労が混ざっており、優先順位を付けると前進しやすい状態です。",
    emotions: ["不安", "焦り", "少しの希望"],
    priorities: [
      "今日やることを3つに絞る",
      "相談できる相手を1人決める",
      "休息時間を予定に固定する",
    ],
  };
}

export async function createActionPlan(result: OrganizedResult): Promise<ActionPlan> {
  await wait(300);

  return {
    title: "48時間アクションプラン",
    steps: [
      `最優先: ${result.priorities[0]}`,
      "明日の午前中に15分だけ着手する",
      "完了後に気分を10段階で記録する",
    ],
  };
}

export async function saveHistory(input: {
  sessionId: string;
  title: string;
  result: OrganizedResult;
  plan: ActionPlan;
}): Promise<HistoryItem> {
  await wait(150);
  return {
    id: uid(),
    title: input.title,
    createdAt: nowText(),
    result: input.result,
    plan: input.plan,
  };
}

export async function loadHistories(): Promise<HistoryItem[]> {
  await wait(250);

  return [
    {
      id: uid(),
      title: "仕事の優先順位を整理",
      createdAt: new Date(Date.now() - 1000 * 60 * 60 * 26).toISOString(),
      result: {
        summary: "タスク過多による焦りが中心。優先順位の明確化で負荷を下げられる状態。",
        emotions: ["焦り", "疲労"],
        priorities: ["重要タスクの絞り込み", "期限交渉", "休憩確保"],
      },
      plan: {
        title: "1日リセットプラン",
        steps: ["朝5分で優先3件を決める", "昼に進捗確認", "夕方に振り返り"],
      },
    },
  ];
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

export type ProblemFilter = {
  theme?: Theme;
  status?: ProblemStatus;
};

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

export type TriageInput = {
  action: TriageAction;
  problemId: string;
  /** editTheme 用 */
  theme?: Theme;
  /** editTitle 用 */
  title?: string;
  /** relink / merge 用（移動する Mention / 統合先 Problem） */
  mentionId?: string;
  targetProblemId?: string;
};

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
