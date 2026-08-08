import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { fakeEntraLogin, LIVE_APP_URL, LIVE_BFF_URL, liveEnvMissing } from "./entra-login";

/**
 * [L4] UX 体験プローブ — **実環境で相談シナリオを複数往復し、記録を残す** (#123 M0 / ADR 0022)。
 *
 * consultation-scenario.spec.ts (結線カナリア: 1 往復で「通るか」) との分担:
 * このプローブは「通るか」ではなく **どんな体験だったか** を構造化 JSON として保存する。
 *   (a) 全応答文 (opener + 各往復の assistant 応答) — UX judge (ux-rubric.md) の採点入力
 *   (b) 区間レイテンシ (発話送信 → tRPC 応答 → 画面表示 → TTS 要求 → TTS 応答) — #120「まず測る」
 *
 * 設計判断:
 * - シナリオは**固定・バージョン付き** (scenario.id)。毎朝同じ入力で流すことで
 *   応答品質・レイテンシの時系列比較が成立する (入力が揺れると劣化検知にならない)
 * - レイテンシ閾値超過は **warn (JSON の warnings + workflow annotation) であり fail にしない**。
 *   #120 は「まず測る」段階で、目標値は計測データが溜まってから PO が決める。
 *   fail するのは対話が壊れているとき (応答なし / stub) のみ — それは監視の仕事
 * - 記録は 1 往復ごとに書き出す (途中で落ちても、そこまでの記録が artifact に残る)
 * - 出力先: UX_PROBE_OUTPUT_DIR (default: probe-results/) — workflow が artifact として保存
 */

test.skip(liveEnvMissing, "LIVE_* env が未設定 (実環境向けのみ実行)");

// 4 往復 × (実 AI + コールドスタート) を許容する。config の既定 240s では足りない
test.setTimeout(900_000);

/** シナリオ変更時は id を上げる (時系列比較の断絶点を明示するため)。 */
const SCENARIO = {
  id: "work-overwhelm-v1",
  description:
    "仕事のタスク過多で眠りが浅い相談者。深掘りに応じて『失敗より失望されるのが怖い』と core が出てくる典型パス",
  userTurns: [
    "最近、仕事のことで頭がいっぱいで眠りが浅いんです。タスクが多すぎて、何から手をつければいいか分からなくて",
    "一番気になっているのは、上司に頼まれた企画書です。期待されている気がして、失敗したらどうしようと考えてしまいます",
    "そう言われてみると、失敗そのものより、上司にがっかりされるのが怖いのかもしれません",
    "少し整理できた気がします。まず何から手をつけるのがいいでしょうか",
  ],
} as const;

// 初期閾値 (超過 = warn)。#120 の正式な目標値 (例: P95) は計測データが溜まってから
// PO が決める — それまでの仮置き。env で上書き可能
const WARN_REPLY_VISIBLE_MS = Number(process.env.UX_PROBE_WARN_REPLY_MS ?? 10_000);
const WARN_TTS_SYNTH_MS = Number(process.env.UX_PROBE_WARN_TTS_MS ?? 8_000);

type TtsRecord = {
  text: string;
  requestAt: number;
  responseAt?: number;
  status?: number;
  contentType?: string;
};

type TurnRecord = {
  index: number;
  userText: string;
  assistantText: string;
  timings: {
    sentAt: string;
    /** 発話送信 → BFF (tRPC sendMessage) 応答受信 */
    sendToTrpcResponseMs: number;
    /** 発話送信 → 応答が画面に表示 (体感レイテンシに最も近い) */
    sendToReplyVisibleMs: number;
    /** 応答表示 → TTS リクエスト送出 (TTS 開始まで) */
    replyVisibleToTtsRequestMs: number | null;
    /** TTS リクエスト → TTS 応答 (合成時間) */
    ttsRequestToResponseMs: number | null;
    /** 発話送信 → TTS 応答 (音声が再生可能になるまでの総計) */
    sendToTtsResponseMs: number | null;
  };
  ttsStatus: number | null;
  warnings: string[];
};

type ProbeRecord = {
  schemaVersion: 1;
  kind: "ux-probe-conversation";
  probeId: string;
  startedAt: string;
  environment: {
    appUrl: string;
    bffUrl: string;
    gitSha: string | null;
    runId: string | null;
    runUrl: string | null;
  };
  scenario: { id: string; description: string; plannedTurns: number };
  thresholds: { warnReplyVisibleMs: number; warnTtsSynthMs: number };
  openerText: string | null;
  turns: TurnRecord[];
  summary: {
    completedTurns: number;
    avgSendToReplyVisibleMs: number | null;
    maxSendToReplyVisibleMs: number | null;
    warningCount: number;
    /** 1 往復目は Container Apps の scale-to-zero コールドスタートを含み得る (ADR 0013) */
    firstTurnIncludesColdStart: true;
  };
};

/** /api/tts の POST を傍受して text ごとの request/response 時刻を記録する。 */
function recordTts(page: Page): TtsRecord[] {
  const records: TtsRecord[] = [];
  const parseText = (postData: string | null): string => {
    try {
      return (JSON.parse(postData ?? "{}") as { text?: string }).text ?? "";
    } catch {
      return "";
    }
  };
  page.on("request", (req) => {
    if (req.url().startsWith(`${LIVE_BFF_URL}/api/tts`) && req.method() === "POST") {
      records.push({ text: parseText(req.postData()), requestAt: Date.now() });
    }
  });
  page.on("response", (res) => {
    const req = res.request();
    if (req.url().startsWith(`${LIVE_BFF_URL}/api/tts`) && req.method() === "POST") {
      const text = parseText(req.postData());
      const rec = records.find((r) => r.text === text && r.responseAt === undefined);
      if (rec) {
        rec.responseAt = Date.now();
        rec.status = res.status();
        rec.contentType = res.headers()["content-type"];
      }
    }
  });
  return records;
}

/** 指定テキストの TTS が応答済みになるまで待つ。TTS 欠落は warn 扱い (fail は結線カナリアの仕事)。 */
async function waitForTtsOf(
  records: TtsRecord[],
  assistantText: string,
  timeoutMs: number,
): Promise<TtsRecord | undefined> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const rec = records.find((r) => r.text === assistantText && r.responseAt !== undefined);
    if (rec) return rec;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return undefined;
}

/** tRPC 応答 (httpBatchLink の配列 / 単体の両形) から reply を取り出す。 */
function extractReply(json: unknown): string {
  const item = (Array.isArray(json) ? json[0] : json) as
    | { result?: { data?: { reply?: string } } }
    | undefined;
  return item?.result?.data?.reply ?? "";
}

test("[L4] UX プローブ: 相談シナリオ 4 往復の全応答文 + 区間レイテンシを記録する", async ({
  page,
}) => {
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log(`[browser:error] ${msg.text()}`);
  });
  page.on("pageerror", (err) => console.log(`[browser:pageerror] ${err.message}`));

  const startedAt = new Date();
  const record: ProbeRecord = {
    schemaVersion: 1,
    kind: "ux-probe-conversation",
    probeId: `ux-probe-${startedAt.toISOString().replace(/[:.]/g, "-")}`,
    startedAt: startedAt.toISOString(),
    environment: {
      appUrl: LIVE_APP_URL,
      bffUrl: LIVE_BFF_URL,
      gitSha: process.env.GITHUB_SHA ?? null,
      runId: process.env.GITHUB_RUN_ID ?? null,
      runUrl:
        process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
          ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
          : null,
    },
    scenario: {
      id: SCENARIO.id,
      description: SCENARIO.description,
      plannedTurns: SCENARIO.userTurns.length,
    },
    thresholds: { warnReplyVisibleMs: WARN_REPLY_VISIBLE_MS, warnTtsSynthMs: WARN_TTS_SYNTH_MS },
    openerText: null,
    turns: [],
    summary: {
      completedTurns: 0,
      avgSendToReplyVisibleMs: null,
      maxSendToReplyVisibleMs: null,
      warningCount: 0,
      firstTurnIncludesColdStart: true,
    },
  };

  const outDir = path.resolve(process.cwd(), process.env.UX_PROBE_OUTPUT_DIR ?? "probe-results");
  const outFile = path.join(outDir, `${record.probeId}.json`);
  const flush = () => {
    const visible = record.turns.map((t) => t.timings.sendToReplyVisibleMs);
    record.summary.completedTurns = record.turns.length;
    record.summary.avgSendToReplyVisibleMs = visible.length
      ? Math.round(visible.reduce((a, b) => a + b, 0) / visible.length)
      : null;
    record.summary.maxSendToReplyVisibleMs = visible.length ? Math.max(...visible) : null;
    record.summary.warningCount = record.turns.reduce((a, t) => a + t.warnings.length, 0);
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(outFile, JSON.stringify(record, null, 2) + "\n");
  };

  const ttsRecords = recordTts(page);
  await fakeEntraLogin(page);

  await page.goto("/");
  await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "新しい相談を始める" }).click();
  await expect(page).toHaveURL(/\/consultations\/current$/);

  // opener (AI 非呼び出しの問いかけ) も採点対象の会話に含める
  const assistantLabels = page.locator("text=ガイド");
  await expect(assistantLabels.first()).toBeVisible({ timeout: 60_000 });
  record.openerText = (await assistantLabels.first().locator("..").innerText())
    .replace(/^ガイド\n?/, "")
    .trim();
  flush();

  const composer = page.getByPlaceholder("ここに入力 / 話して入力");

  for (let i = 0; i < SCENARIO.userTurns.length; i++) {
    const userText = SCENARIO.userTurns[i];

    const trpcPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/trpc/consultation.sendMessage") &&
        res.request().method() === "POST",
      { timeout: 210_000 },
    );

    await composer.fill(userText);
    const sentAt = Date.now();
    await page.getByRole("button", { name: "送信" }).click();

    const trpcRes = await trpcPromise;
    const trpcAt = Date.now();
    expect(trpcRes.status(), `turn ${i + 1}: sendMessage status`).toBe(200);
    const assistantText = extractReply(await trpcRes.json());

    // 対話が壊れているケースだけ fail (品質・レイテンシの評価は judge / warn の仕事)
    expect(assistantText, `turn ${i + 1}: 実 AI の応答が空`).not.toBe("");
    expect(assistantText, `turn ${i + 1}: stub 応答 (AI_AGENT_BASE_URL 結線切れ)`).not.toContain(
      "[stub]",
    );

    // 応答の実テキストで描画を待つ ("ガイド" ラベルの nth 指定は応答文自体に
    // 「ガイド」が含まれたとき index がずれるため使わない)
    const excerpt =
      assistantText.split("\n")[0].trim().slice(0, 40) || assistantText.trim().slice(0, 40);
    await expect(page.getByText(excerpt).last()).toBeVisible({ timeout: 60_000 });
    const visibleAt = Date.now();

    // TTS は応答描画後に非同期で走る (Layout が応答全文を text にして呼ぶ)。
    // 欠落・失敗は warn (200 + audio/wav の保証は consultation-scenario / golden-path hop5 が担当)
    const tts = await waitForTtsOf(ttsRecords, assistantText, 120_000);

    const warnings: string[] = [];
    const sendToReplyVisibleMs = visibleAt - sentAt;
    if (sendToReplyVisibleMs > WARN_REPLY_VISIBLE_MS) {
      warnings.push(
        `send→reply表示 ${sendToReplyVisibleMs}ms > 閾値 ${WARN_REPLY_VISIBLE_MS}ms (#120)`,
      );
    }
    const ttsSynthMs = tts?.responseAt !== undefined ? tts.responseAt - tts.requestAt : null;
    if (ttsSynthMs !== null && ttsSynthMs > WARN_TTS_SYNTH_MS) {
      warnings.push(`TTS合成 ${ttsSynthMs}ms > 閾値 ${WARN_TTS_SYNTH_MS}ms (#120)`);
    }
    if (tts === undefined) {
      warnings.push("TTS 応答を 120s 以内に観測できず (レイテンシ計測は欠測)");
    } else if (tts.status !== 200) {
      warnings.push(`TTS status ${tts.status} (200 以外 — 音声なしの体験になっている)`);
    }

    record.turns.push({
      index: i + 1,
      userText,
      assistantText,
      timings: {
        sentAt: new Date(sentAt).toISOString(),
        sendToTrpcResponseMs: trpcAt - sentAt,
        sendToReplyVisibleMs,
        replyVisibleToTtsRequestMs: tts ? tts.requestAt - visibleAt : null,
        ttsRequestToResponseMs: ttsSynthMs,
        sendToTtsResponseMs: tts?.responseAt !== undefined ? tts.responseAt - sentAt : null,
      },
      ttsStatus: tts?.status ?? null,
      warnings,
    });
    flush();
    console.log(
      `[ux-probe] turn ${i + 1}/${SCENARIO.userTurns.length}: reply ${sendToReplyVisibleMs}ms, ` +
        `tts ${ttsSynthMs ?? "n/a"}ms, warnings ${warnings.length}`,
    );
  }

  flush();
  console.log(`[ux-probe] record written: ${outFile}`);
});
