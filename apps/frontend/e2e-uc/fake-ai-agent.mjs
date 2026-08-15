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
    key: "study",
    keywords: ["勉強", "資格", "学び直し", "身につか"],
    title: "学び直しが続かない",
    theme: "自己理解・生き方",
    statement: "学び直そうと決めても続かず、身についている実感がない",
    tags: ["学習習慣"],
    affect: { label: "焦り", valence: "negative", intensity: 0.5 },
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
 * 各 spec が「自分の題材」を 1 つ占有できるように 8 種類用意している。
 * BFF の Problem リポジトリは E2E の全 spec で共有される (プロセスが 1 本) ため、
 * 題材が被ると別 spec が作った Problem に寄ってしまい、テストが順序に依存する。
 */

/** セッションごとのユーザー発話。/extract はここを読んで抽出する。 */
const sessions = new Map();

/**
 * 副作用ツールの承認 (G1 / ADR 0016 M1-3) を決定的に踏むためのトリガ。
 *
 * 実 ai-agent は LLM が `approval_mode="always_require"` のツール (`send_reply` 等) を
 * 選んだときだけ承認待ちになる。LLM の判断はこの層の関心ではない (差し替えているのは
 * それだけ) ので、語彙一致で同じ HTTP 契約を返す。
 * 文面は実装 (`app/workflow.py`) と同じにしてある — 実物と別物に見えると、承認 UI が
 * 「fake でだけ通る」形に寄っていくため。
 */
const APPROVAL_TRIGGER = "返信して";
const APPROVAL_TOOL = "send_reply";
/** 実装の `_REJECTION_REPLY` と同一文面。 */
const REJECTION_REPLY = "操作はキャンセルされました。他にご用件はありますか？";

/**
 * 選択肢の提示 (#432-b / dialogue-session.mdx §5.10) を決定的に踏むためのトリガ。
 *
 * 実 ai-agent は LLM が `offer_choices` ツールを選んだときに `choices` を返す。
 * ここも承認と同じく語彙一致で置き換える。**上限 (3 件) は実装と同じ**にしておく —
 * ハーネスだけ緩いと「本番では出ない件数」を通したテストが緑になる。
 */
const CHOICES_TRIGGER = "わからない";
const CHOICES = ["仕事のこと", "家族やパートナーのこと", "自分の体調のこと"];
const CHOICES_REPLY = "近いものがあれば選んでみてください。自由に書いてもらっても大丈夫です。";

/**
 * approval_request_id → { status, processedAt }。
 * 二重解決 (実装は **409 + 現在状態** / #82) を再現するために持つ。
 */
const approvals = new Map();

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

/** 副作用ツールを実行しようとした = 承認待ちの応答 (G1)。 */
function needsApproval(message) {
  return message.includes(APPROVAL_TRIGGER);
}

/** 承認待ちの ChatResponse。実装 (`workflow.py`) と同じ形・同じ文面で返す。 */
function approvalPending() {
  const id = nextId("appr");
  approvals.set(id, { status: "pending", processedAt: null });
  return {
    reply: `「${APPROVAL_TOOL}」を実行するには承認が必要です。実行してよろしいですか？`,
    requires_approval: true,
    approval_request_id: id,
    citations: [],
    // 承認要求のターンに選択肢は載せない (#432-b / 実装の `_record_approval_request`)。
    // ここを混ぜると「承認カードと選択肢が同時に出る画面」を fake が正当化してしまう
    choices: [],
  };
}

/**
 * 1 ターン分の ChatResponse (実装 `schemas.ChatResponse` と同じ形)。
 *
 * **選択肢は「会話の分岐」なのでサーバ側に状態を持たない** (完了型 / PO 裁定
 * 2026-08-15)。承認 (`approvals` Map) と違い、押されたことを覚える場所も、
 * 押されなかったことを解決する口も**意図的に無い** — 押した文言は次の
 * `/chat/stream` に普通の発話として届くだけ。
 */
function chatResponse(message) {
  if (needsApproval(message)) return approvalPending();
  if (message.includes(CHOICES_TRIGGER)) {
    return {
      reply: CHOICES_REPLY,
      requires_approval: false,
      approval_request_id: null,
      citations: [],
      choices: CHOICES,
    };
  }
  return {
    reply: replyTo(message),
    requires_approval: false,
    approval_request_id: null,
    citations: [],
    choices: [],
  };
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
    return { status: 200, json: chatResponse(body.message) };
  },

  /**
   * 承認 / 却下で中断していたツール実行を解決する (G1)。
   *
   * 実装 (`workflow.py` / `main.py`) と同じ分け方にする (#82 / PO 裁定 2026-08-15 B 案):
   * **未知 ID は 404 / 解決済み ID は 409 + 現在状態**。ここを 404 に丸めると、
   * ハーネスだけが古い契約のままになり「実配線で確かめた」と言えなくなる。
   */
  "POST /approve": (body) => {
    const id = body.approval_request_id;
    const record = approvals.get(id);
    if (!record) {
      return { status: 404, json: { detail: `Approval not found: '${id}'` } };
    }
    if (record.status !== "pending") {
      return {
        status: 409,
        json: {
          detail: `Approval already processed: '${record.status}'`,
          status: record.status,
          processed_at: record.processedAt,
        },
      };
    }
    approvals.set(id, {
      status: body.approved ? "approved" : "rejected",
      processedAt: new Date().toISOString(),
    });
    return {
      status: 200,
      json: {
        reply: body.approved ? `[stub] Reply sent to team@example.com.` : REJECTION_REPLY,
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
      // 承認待ちも選択肢もストリーミング経路で起きる (実装は同じ workflow を stream で
      // 回す)。ここを非ストリーミングだけにすると、フロントの主経路 (SSE) で
      // 承認要求 / 選択肢が運ばれるかを一度も踏まないまま緑になる。
      const response = chatResponse(body.message);
      const reply = response.reply;

      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      for (let i = 0; i < reply.length; i += 8) {
        res.write(`data: ${JSON.stringify({ type: "delta", text: reply.slice(i, i + 8) })}\n\n`);
      }
      res.write(`data: ${JSON.stringify({ type: "done", response })}\n\n`);
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
