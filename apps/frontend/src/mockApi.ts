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
  | "crisisSupport";

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
    title: concern || "相談セッション",
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
