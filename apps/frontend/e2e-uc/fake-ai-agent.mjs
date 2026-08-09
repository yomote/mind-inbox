#!/usr/bin/env node
/**
 * UC 受け入れ E2E 用の **ai-agent テストダブル**。
 *
 * なぜ「BFF の stub」ではなくこれを立てるのか:
 *
 * BFF は AI_AGENT_BASE_URL 未設定時に stub を返すが、その stub は毎回同じ
 * 「[stub] 困りごと」1 件を新規で返すだけで、**既存 Problem への寄せ (段2) が起きない**。
 * つまり UC-03 (繰り返しに気づく) と UC-04 (棚卸し後の再燃) が原理的に踏めない。
 * ここでは実 ai-agent と同じ HTTP 契約のまま、キーワードで決定的にグルーピングする
 * ダブルを置き、**BFF 側のコードは一切変えずに** ユースケースを通す。
 *
 * 差し替えているのは「LLM の判断」だけ。BFF の materializeExtraction / Problem
 * リポジトリ / tRPC / フロントの状態遷移はすべて本物が動く。
 *
 * env: PORT (既定 8099)
 */

import { createServer } from "node:http";

const PORT = Number(process.env.PORT || 8099);

/**
 * キーワード → 困りごとの型。LLM の意味類似の代わりに、決定的な語彙一致で寄せる。
 * `key` は BFF が渡してくる既存 Problem のタイトルとの突き合わせにも使う。
 */
const TOPICS = [
  {
    key: "priority",
    keywords: ["優先順位", "やること", "タスク", "手が回らない"],
    title: "やることが多すぎて優先順位をつけられない",
    theme: "仕事・キャリア",
    statement: "抱えているタスクが多く、どれから手をつけるか決められない",
    tags: ["タスク管理", "仕事量"],
    affect: { label: "焦り", valence: "negative", intensity: 0.7 },
  },
  {
    key: "sleep",
    keywords: ["眠れない", "寝れない", "睡眠", "疲れ"],
    title: "眠りが浅くて疲れが取れない",
    theme: "心と体",
    statement: "睡眠が十分に取れず、日中の疲労が抜けない",
    tags: ["睡眠", "疲労"],
    affect: { label: "不安", valence: "negative", intensity: 0.6 },
  },
  {
    key: "money",
    keywords: ["お金", "家計", "貯金", "支出"],
    title: "お金の先行きが不安",
    theme: "お金",
    statement: "収支の見通しが立たず、将来の出費に不安がある",
    tags: ["家計"],
    affect: { label: "不安", valence: "negative", intensity: 0.5 },
  },
  {
    key: "relations",
    keywords: ["上司", "同僚", "職場の人", "言いづらい"],
    title: "職場で言いたいことを言えない",
    theme: "人間関係",
    statement: "職場で自分の考えを伝えられず、飲み込んでしまう",
    tags: ["職場", "自己主張"],
    affect: { label: "もどかしさ", valence: "negative", intensity: 0.55 },
  },
  {
    key: "meaning",
    keywords: ["このままでいい", "やりたいこと", "生き方", "何がしたい"],
    title: "このままでいいのか分からない",
    theme: "自己理解・生き方",
    statement: "今の生き方を続けてよいのか判断できずにいる",
    tags: ["キャリア観"],
    affect: { label: "迷い", valence: "neutral", intensity: 0.5 },
  },
  {
    key: "chores",
    keywords: ["家事", "片付け", "掃除", "部屋が"],
    title: "家事が回らず部屋が荒れる",
    theme: "日常・生活",
    statement: "家事に手が回らず、生活空間が整わない",
    tags: ["家事"],
    affect: { label: "うんざり", valence: "negative", intensity: 0.45 },
  },
  {
    // #183 の「ai-agent が会話を忘れていても抽出できる」専用。他 spec と題材を重ねない。
    key: "exercise",
    keywords: ["運動", "体を動かす", "ジム"],
    title: "運動する時間が取れない",
    theme: "心と体",
    statement: "動きたいのに時間が取れず、体がなまっている",
    tags: ["運動"],
    affect: { label: "もどかしさ", valence: "negative", intensity: 0.35 },
  },
  {
    key: "commute",
    keywords: ["通勤", "満員電車"],
    title: "通勤がしんどい",
    theme: "日常・生活",
    statement: "通勤の負担が大きく、一日の始まりから消耗する",
    tags: ["通勤"],
    affect: { label: "疲弊", valence: "negative", intensity: 0.4 },
  },
];

/**
 * 各 spec が「自分の題材」を 1 つ占有できるように 6 種類用意している。
 * BFF の Problem リポジトリは E2E の全 spec で共有される (プロセスが 1 本) ため、
 * 題材が被ると別 spec が作った Problem に寄ってしまい、テストが順序に依存する。
 */

/** セッションごとのユーザー発話。/extract はここを読んで抽出する。 */
const sessions = new Map();

/**
 * createdAt を単調増加させるためのカウンタ。
 * 「今日」表示を保ちたいので基点は起動時刻にし、1 件ごとに 1 秒進めて順序を固定する。
 */
const startedAt = Date.now();
let tick = 0;
const nextIso = () => new Date(startedAt + tick++ * 1000).toISOString();

let idSeq = 0;
const nextId = (prefix) => `${prefix}-${++idSeq}`;

function record(sessionId, message) {
  const messages = sessions.get(sessionId) ?? [];
  messages.push(message);
  sessions.set(sessionId, messages);
}

/** 受け止め → 掘り下げの定型。実 AI の品質検証はここの担当ではない (L4 / ux-probe)。 */
function replyTo(message) {
  const topic = TOPICS.find((t) => t.keywords.some((k) => message.includes(k)));
  if (!topic) {
    return "そうだったんですね。もう少し聞かせてください。";
  }
  return `${topic.statement}、という感じでしょうか。いつ頃からそう感じていますか?`;
}

/** BFF が渡してくる既存 Problem 候補から、同じ型のものを探す (段2 グルーピング)。 */
function findExisting(existingProblems, topic) {
  return existingProblems.find((p) => p.title === topic.title);
}

/**
 * @param givenMessages 呼び出し側が同送した会話 (#183)。実サービスと同じく**あればこちらを優先**する。
 *   実サービスのセッション履歴はプロセスメモリで、scale-to-zero・スケールアウト・リビジョン
 *   差し替えのいずれでも消える。ここを実装しないと、この層は「履歴が生きている前提」でしか
 *   成功パスを通せず、本番で壊れる条件を一度も踏めない。
 */
function extract(sessionId, existingProblems, givenMessages = []) {
  const messages = givenMessages.length > 0 ? givenMessages : (sessions.get(sessionId) ?? []);
  const spoken = messages.join("\n");

  // 同じ型は 1 セッション 1 Mention に畳む (同じ話を 2 回言っても 2 件にはしない)。
  const matched = TOPICS.filter((t) => t.keywords.some((k) => spoken.includes(k)));

  const items = matched.map((topic) => {
    const existing = findExisting(existingProblems, topic);
    const createdAt = nextIso();

    const mention = {
      id: nextId("m"),
      sessionId,
      dumpId: null,
      createdAt,
      statement: topic.statement,
      excerpt: messages.find((m) => topic.keywords.some((k) => m.includes(k))) ?? spoken,
      affect: topic.affect,
      proposedTheme: topic.theme,
      proposedTags: topic.tags,
      problemId: existing ? existing.id : null,
      groupingConfidence: existing ? 0.92 : null,
    };

    const grouping = existing
      ? {
          kind: "existing",
          problemId: existing.id,
          problemTitle: existing.title,
          problemTheme: existing.theme,
          isRecurrence: true,
          mentionCount: existing.mention_count + 1,
          // 棚卸し済み (resolved / shelved) をまた話した = 再燃 (UC-03)
          reignited: existing.status !== "open",
          groupingConfidence: 0.92,
        }
      : {
          kind: "new",
          problemId: nextId("p"),
          problemTitle: topic.title,
          problemTheme: topic.theme,
          isRecurrence: false,
          mentionCount: 1,
          reignited: false,
          groupingConfidence: null,
        };

    if (grouping.kind === "new") mention.problemId = grouping.problemId;
    return { mention, grouping };
  });

  return {
    sessionId,
    items,
    newProblemCount: items.filter((i) => i.grouping.kind === "new").length,
    updatedProblemCount: items.filter((i) => i.grouping.kind === "existing").length,
  };
}

const encoder = new TextEncoder();

const routes = {
  "POST /chat": (body) => {
    record(body.session_id, body.message);
    return {
      status: 200,
      json: {
        reply: replyTo(body.message),
        requires_approval: false,
        approval_request_id: null,
        citations: [],
      },
    };
  },

  "POST /extract": (body) => {
    // 実サービスに合わせ、会話が渡っていれば履歴を見ない (#183)。
    // ユーザー発話だけを見る (抽出対象は吐き出しであって AI の相槌ではない)。
    const given = (body.messages ?? []).filter((m) => m.role === "user").map((m) => m.text);
    return {
      status: 200,
      json: extract(body.session_id, body.existing_problems ?? [], given),
    };
  },

  /**
   * テスト用: セッション履歴だけを捨てる (#183)。
   * scale-to-zero でレプリカが落ちた / スケールアウトで別レプリカに当たった状況を作る。
   */
  "POST /__drop-sessions": () => {
    sessions.clear();
    return { status: 200, json: { ok: true } };
  },

  "POST /organize": (body) => {
    const messages = sessions.get(body.session_id) ?? [];
    const matched = TOPICS.filter((t) => t.keywords.some((k) => messages.join("\n").includes(k)));
    return {
      status: 200,
      json: {
        summary: matched.length
          ? matched.map((t) => t.statement).join(" / ")
          : "話を受け止めました。",
        emotions: [...new Set(matched.map((t) => t.affect.label))],
        priorities: matched.flatMap((t) => t.tags),
      },
    };
  },

  "POST /plan": (body) => ({
    status: 200,
    json: {
      title: `${body.summary.slice(0, 20)} への次の一歩`,
      steps: ["いちばん小さい一歩を1つ選ぶ", "15 分だけ手を付ける", "やった結果をメモする"],
    },
  }),

  "GET /health": () => ({ status: 200, json: { status: "ok" } }),
};

const server = createServer((req, res) => {
  void (async () => {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

    // SSE は「実 ai-agent と同じく逐次で届く」ことをフロントに踏ませたいので別扱い。
    if (req.method === "POST" && url.pathname === "/chat/stream") {
      const body = await readJson(req);
      record(body.session_id, body.message);
      const reply = replyTo(body.message);

      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      for (let i = 0; i < reply.length; i += 8) {
        res.write(`data: ${JSON.stringify({ type: "delta", text: reply.slice(i, i + 8) })}\n\n`);
      }
      res.write(
        `data: ${JSON.stringify({
          type: "done",
          response: {
            reply,
            requires_approval: false,
            approval_request_id: null,
            citations: [],
          },
        })}\n\n`,
      );
      res.end();
      return;
    }

    const route = routes[`${req.method} ${url.pathname}`];
    if (!route) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: `no route for ${req.method} ${url.pathname}` }));
      return;
    }

    const result = route(req.method === "GET" ? {} : await readJson(req));
    const payload = encoder.encode(JSON.stringify(result.json));
    res.writeHead(result.status, {
      "Content-Type": "application/json",
      "Content-Length": payload.byteLength,
    });
    res.end(payload);
  })().catch((err) => {
    console.error("[fake-ai-agent]", err);
    if (!res.headersSent) res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: String(err) }));
  });
});

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[fake-ai-agent] listening on http://127.0.0.1:${PORT}`);
});
