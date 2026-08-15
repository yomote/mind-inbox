// @ts-check
/**
 * [L0] BFF zod ↔ ai-agent pydantic の I/O 契約を **ネストまで再帰的に** 比較する。
 *
 * 無いと何が静かに通るか:
 *   片側 (BFF or ai-agent) でフィールドを追加 / 削除 / 型変更 / nullable 化 / optional 化 /
 *   enum メンバの増減をしても、もう片側を直し忘れた状態で merge できてしまう。
 *   本番で「BFF が送ったフィールドを ai-agent が無視する」「ai-agent が返す null で BFF が落ちる」
 *   「Theme の分類が片側だけ増えて未知値が飛ぶ」といった silent な不整合が起きる。
 *
 * 比較粒度 (詳細と表層差の吸収ルールは src/contract/schemaDiff.ts のヘッダに集約):
 *   - フィールドパス集合 (`items[].mention.affect.valence` まで再帰展開)
 *   - 型カテゴリ / nullable / 欠落可能性 (optional) / enum メンバ集合
 *   - schema に乗らないが BFF の分岐が依存している契約: `/approve` の 404 detail (#82)
 *
 * ここで test しないこと:
 *   - 個別フィールドの値検証 (それは L2 endpoint test の領域)
 *   - 数値の min/max・description・additionalProperties などの注釈差 (表層差として無視)
 */

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";

// src/*.ts は拡張子なしの相対 import を使う (tsconfig は commonjs) ので、
// ESM 解決に `.ts` を補う hook を先に登録してから動的 import する。
register("./ts-extension-resolver.mjs", import.meta.url);

const { diffSchemas } = await import("../src/contract/schemaDiff.ts");
const { ExtractionResultSchema } = await import("../src/trpc/domain.ts");
const {
  APPROVAL_GONE_DETAIL,
  ApprovalConflictSchema,
  ApproveRequestSchema,
  ApproveResponseSchema,
  ChatRequestSchema,
  ChatResponseSchema,
  ChatStreamDeltaSchema,
  ChatStreamDoneSchema,
  ChatStreamErrorSchema,
  ExtractRequestSchema,
  PlanRequestSchema,
  PlanResponseSchema,
} = await import("../src/clients/aiAgentContracts.ts");

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "../../..");

// ── 比較対象 ────────────────────────────────────────────────────────────────
//
// key は ai-agent の export 名 (cicd/scripts/testing/export-ai-agent-schemas.py の SCHEMAS) と
// 1:1 で対応する。ai-agent のエンドポイント 7 本 (/chat /chat/stream /extract /plan
// /approve /health) のうち、BFF が型を持つ I/O をすべて載せる (/health は BFF に型が無い)。
const BFF_SCHEMAS = {
  ChatRequest: ChatRequestSchema,
  ChatResponse: ChatResponseSchema,
  ChatStreamDelta: ChatStreamDeltaSchema,
  ChatStreamDone: ChatStreamDoneSchema,
  ChatStreamError: ChatStreamErrorSchema,
  // `problem.createPlan` は Problem の summary / affect ラベル / tags を pluck して
  // ai-agent に渡す (apps/bff/src/trpc/router.ts problem.createPlan 参照)。
  // 比較するのはその「pluck 後の payload」。
  PlanRequest: PlanRequestSchema,
  PlanResponse: PlanResponseSchema,
  ApproveRequest: ApproveRequestSchema,
  ApproveResponse: ApproveResponseSchema,
  // `/approve` の 409 body (#82)。200 と同じ粒度で比較する — 二重送信の応答は
  // フロントが「操作が実行されたか」を言い切る唯一の材料なので、片側だけ形を
  // 変えると「すでに承認済みです」の案内が黙って汎用エラーに落ちる。
  ApprovalConflictResponse: ApprovalConflictSchema,
  ExtractRequest: ExtractRequestSchema,
  ExtractionResult: ExtractionResultSchema,
};

// ── ai-agent schema fetch ───────────────────────────────────────────────────

function fetchAiAgentSchemas() {
  const script = resolve(REPO_ROOT, "cicd/scripts/testing/export-ai-agent-schemas.py");
  const out = execFileSync(
    "uv",
    ["run", "--directory", "apps/services/ai-agent", "python", script],
    { cwd: REPO_ROOT, encoding: "utf-8", stdio: ["ignore", "pipe", "inherit"] },
  );
  return JSON.parse(out);
}

// ── /approve の 404 detail (schema に乗らない契約 / #82 / PR #416) ────────────
//
// 無いと何が静かに通るか: BFF は `/approve` の 404 のうち **ai-agent が承認レコードに
// ついて返したもの**だけを「もう受け付けられない」に変換する (それ以外の 404 =
// ルート未配備 / プロキシの経路不整合は上流障害として扱う)。判別は detail 文字列との
// 一致なので、ai-agent 側が文言を変えると**変換が静かに効かなくなり**、失効した承認が
// 上流障害に化けて 404 デッドロック (承認カードが閉じられず会話が進まない) が戻る。
// pydantic schema にはエラー本文が現れないので、上の再帰比較では捕まらない。
//
// **BFF 側のパターンは ai-agent の現行 404 の集合より広くてよい** (#430 judge major-2):
// `Approval already processed` は現行 ai-agent では 409 に移ったが、配備スキューの窓で
// 旧 ai-agent が 404 で返すので判別に残してある。ここが見るのは「ai-agent が返す 404 が
// すべて BFF に判別されるか」の一方向だけ — 逆向き (BFF にだけある古い文言) は
// スキュー耐性なので落とさない。

/** ai-agent 側で 404 の detail を作っている場所 (`main.py` が `detail=str(exc)` で載せる)。 */
const APPROVAL_DETAIL_SOURCE = "apps/services/ai-agent/app/workflow.py";
const APPROVAL_DETAIL_FUNCTION = "async def resume_after_approval(";

function checkApprovalGoneDetail() {
  const path = resolve(REPO_ROOT, APPROVAL_DETAIL_SOURCE);
  // 読めなければ「一致した」ではなく失敗として出す (取れなかったものを異常なしと書かない)。
  const source = readFileSync(path, "utf-8");

  const start = source.indexOf(APPROVAL_DETAIL_FUNCTION);
  if (start < 0) {
    console.error(
      `  ✗ ApprovalGoneDetail: ${APPROVAL_DETAIL_SOURCE} に ${APPROVAL_DETAIL_FUNCTION} が無い (関数名が変わった?)`,
    );
    return 1;
  }
  // 次の top-level def までを関数本体とみなす。
  const rest = source.slice(start + APPROVAL_DETAIL_FUNCTION.length);
  const end = rest.search(/\n(?:async )?def /);
  const body = end < 0 ? rest : rest.slice(0, end);

  // `raise ValueError(f"...")` の文言だけ抜く (404 に写るのは ValueError だけ / main.py)。
  const messages = [...body.matchAll(/raise ValueError\(\s*f?"([^"]*)"/g)].map((m) => m[1]);
  if (messages.length === 0) {
    console.error(
      `  ✗ ApprovalGoneDetail: ${APPROVAL_DETAIL_SOURCE} の resume_after_approval に ValueError が 1 件も無い (404 の出どころが変わった?)`,
    );
    return 1;
  }

  const unmatched = messages.filter((m) => !APPROVAL_GONE_DETAIL.test(m));
  if (unmatched.length > 0) {
    console.error(
      `  ✗ ApprovalGoneDetail: BFF の判別パターン ${APPROVAL_GONE_DETAIL} に一致しない 404 detail が ai-agent にある:`,
    );
    for (const m of unmatched) console.error(`      - ${m}`);
    console.error(
      "      → この detail の 404 は BFF が「上流障害」として扱う (承認カードが閉じられず会話が詰まる)。",
    );
    return unmatched.length;
  }

  out(`  ✓ ApprovalGoneDetail: /approve の 404 detail ${messages.length} 件 すべて判別可能`);
  return 0;
}

// ── /approve の 409 (二重送信 / #82 / PO 裁定 2026-08-15 B 案) ────────────────
//
// 無いと何が静かに通るか: BFF は `res.status === 409` を見て初めて
// `ApprovalAlreadyProcessedError` を立て、フロントは「すでに承認済み (= 実行された)」
// と言い切る。ai-agent 側が二重送信を再び ValueError (= 404) に戻すと、**BFF の 409
// 分岐は一度も通らなくなる**が、両者のテストは片側ずつ緑のまま通る (BFF は自分で
// 409 を作ってテストしているため)。結果、UI は「実行されたか分かりません」に静かに
// 逆戻りし、送信済みのメールを再送させる事故が戻る。
// body の**形**は上の再帰比較 (ApprovalConflictResponse) が見るので、ここでは
// 「二重送信がその形の 409 に写っているか」だけを見る。

/** 二重送信で投げる例外クラス名 (ValueError = 404 側と別の型であることが契約の本体)。 */
const APPROVAL_CONFLICT_EXC = "ApprovalAlreadyProcessedError";
const APPROVAL_ENDPOINT_SOURCE = "apps/services/ai-agent/app/main.py";

function checkApprovalConflict() {
  const workflowPath = resolve(REPO_ROOT, APPROVAL_DETAIL_SOURCE);
  const source = readFileSync(workflowPath, "utf-8");
  const start = source.indexOf(APPROVAL_DETAIL_FUNCTION);
  if (start < 0) {
    console.error(
      `  ✗ ApprovalConflict: ${APPROVAL_DETAIL_SOURCE} に ${APPROVAL_DETAIL_FUNCTION} が無い (関数名が変わった?)`,
    );
    return 1;
  }
  const rest = source.slice(start + APPROVAL_DETAIL_FUNCTION.length);
  const end = rest.search(/\n(?:async )?def /);
  const body = end < 0 ? rest : rest.slice(0, end);

  if (!new RegExp(`raise ${APPROVAL_CONFLICT_EXC}\\(`).test(body)) {
    console.error(
      `  ✗ ApprovalConflict: resume_after_approval が ${APPROVAL_CONFLICT_EXC} を投げていない`,
    );
    console.error(
      "      → 二重送信が 404 (レコードが無い) に混ざる = フロントは「実行されたか」を言えなくなる。",
    );
    return 1;
  }

  // endpoint 側: その例外が 409 + ApprovalConflictResponse に写っていること。
  const mainSource = readFileSync(resolve(REPO_ROOT, APPROVAL_ENDPOINT_SOURCE), "utf-8");
  const handler = mainSource.slice(mainSource.indexOf(`except ${APPROVAL_CONFLICT_EXC}`));
  const mapped =
    handler.length > 0 &&
    /status_code=409/.test(handler.slice(0, 800)) &&
    /ApprovalConflictResponse\(/.test(handler.slice(0, 800));
  if (!mapped) {
    console.error(
      `  ✗ ApprovalConflict: ${APPROVAL_ENDPOINT_SOURCE} が ${APPROVAL_CONFLICT_EXC} を 409 + ApprovalConflictResponse に写していない`,
    );
    console.error(
      "      → BFF の 409 分岐が一度も通らなくなる (「すでに承認済みです」の案内が汎用エラーに落ちる)。",
    );
    return 1;
  }

  out("  ✓ ApprovalConflict: 二重送信は 409 + ApprovalConflictResponse に写っている");
  return 0;
}

// ── main ────────────────────────────────────────────────────────────────────

/** stdout への成功系出力 (CLI ツールとしての結果表示。debug 用 console.log ではない) */
function out(line) {
  process.stdout.write(`${line}\n`);
}

function main() {
  out("[L0 contract] BFF zod ↔ ai-agent pydantic 比較を開始 (ネスト再帰 / required / enum)");

  const agentSchemas = fetchAiAgentSchemas();

  let totalIssues = 0;
  for (const [name, zodSchema] of Object.entries(BFF_SCHEMAS)) {
    const agent = agentSchemas[name];
    if (!agent) {
      console.error(`[FAIL] ai-agent 側に schema ${name} が見つからない`);
      totalIssues++;
      continue;
    }
    // zod v4 の z.toJSONSchema (#449)。`reused: "inline"` = 旧 zod-to-json-schema の
    // `$refStrategy: "none"` 相当で内部 $ref を作らない (pydantic 側の $defs は比較側が解決する)。
    // nullable は anyOf+null / default は required に残る形で出るが、どちらも
    // src/contract/schemaDiff.ts の正規化 (unwrapNullable / hasDefault) が吸収する。
    const bffSchema = z.toJSONSchema(zodSchema, { reused: "inline" });
    const { issues, fieldCount } = diffSchemas(bffSchema, agent);

    if (issues.length === 0) {
      out(`  ✓ ${name}: フィールドパス ${fieldCount} 件 一致`);
    } else {
      console.error(`  ✗ ${name}: 不整合 ${issues.length} 件`);
      for (const i of issues) console.error(i);
      totalIssues += issues.length;
    }
  }

  // ai-agent 側にだけ増えた schema (BFF が追随していない) も検出する
  for (const name of Object.keys(agentSchemas)) {
    if (!(name in BFF_SCHEMAS)) {
      console.error(`  ✗ ${name}: ai-agent 側にしか schema が無い (BFF の zod が未追随)`);
      totalIssues++;
    }
  }

  totalIssues += checkApprovalGoneDetail();
  totalIssues += checkApprovalConflict();

  if (totalIssues > 0) {
    console.error(
      `\n[L0 contract] ${totalIssues} 件の不整合を検出。BFF zod or ai-agent pydantic のどちらか一方の変更が漏れている可能性があります。`,
    );
    process.exit(1);
  }
  out("\n[L0 contract] OK — 全契約一致");
}

main();
