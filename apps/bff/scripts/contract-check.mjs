// @ts-check
/**
 * [L0] BFF zod ↔ ai-agent pydantic の I/O 契約 (フィールド集合 + 型カテゴリ) を比較する。
 *
 * 無いと何が静かに通るか:
 *   片側 (BFF or ai-agent) に新フィールドを追加 / 既存フィールドを削除 / 型カテゴリを変更しても、
 *   もう片側を直し忘れた状態で merge できてしまう。本番で「BFF が send したフィールドを ai-agent が ignore」
 *   などの silent な不整合が発生する。
 *
 * ここで test しないこと:
 *   - 個別フィールドの値検証 (それは L2 endpoint test の領域)
 *   - 詳細な JSON Schema 完全一致 (snake_case ↔ camelCase の差異など、表層の差は想定通り。
 *     深い型一致検証は OpenAPI 自動生成と組み合わせて別 PR で行う)
 *
 * 比較粒度:
 *   - フィールド集合 (camelCase ↔ snake_case を機械的に正規化して同一性をチェック)
 *   - 型カテゴリ (string / array<string> / boolean) — これだけでも「array → string」のような
 *     危険な変更は捕まる
 */

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { zodToJsonSchema } from "zod-to-json-schema";

import { ActionPlanSchema, OrganizedResultSchema } from "../src/trpc/domain.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "../../..");

// ── BFF zod schemas ─────────────────────────────────────────────────────────
//
// `consultation.createPlan` の input は `{ result: OrganizedResultSchema }` で受け取り
// router 側で `summary / emotions / priorities` を pluck して ai-agent に渡している
// (apps/bff/src/trpc/router.ts createPlan 参照)。L0 ではその「pluck 後の payload」が
// ai-agent の PlanRequest と一致するかを比較する。
const CreatePlanInputPlucked = z.object({
  summary: z.string(),
  emotions: z.array(z.string()),
  priorities: z.array(z.string()),
});

// `consultation.approve` の input — camelCase を ai-agent 側の snake_case (approval_request_id)
// に合わせる対応関係は normalizeKey() で吸収する。
const ApproveInputBff = z.object({
  approvalRequestId: z.string(),
  approved: z.boolean(),
});

const BFF_SCHEMAS = {
  // BFF schema name : zod schema (Node 側 = camelCase 想定)
  OrganizeResponse: OrganizedResultSchema,
  PlanRequest: CreatePlanInputPlucked,
  PlanResponse: ActionPlanSchema,
  ApproveRequest: ApproveInputBff,
};

// ── helpers ─────────────────────────────────────────────────────────────────

/** camelCase → snake_case (機械的) */
function normalizeKey(key) {
  return key.replace(/([A-Z])/g, "_$1").toLowerCase();
}

/**
 * JSON Schema の properties から { fieldName(snake_case): typeCategory } を抽出。
 * typeCategory は粒度を粗く保つ ("array<string>" / "string" / "boolean" / "object" / "unknown")。
 */
function extractFieldTypes(jsonSchema) {
  const properties = jsonSchema?.properties ?? {};
  /** @type {Record<string, string>} */
  const out = {};
  for (const [rawName, def] of Object.entries(properties)) {
    const name = normalizeKey(rawName);
    out[name] = categorizeType(def);
  }
  return out;
}

function categorizeType(def) {
  if (!def || typeof def !== "object") return "unknown";
  // anyOf (Optional[T]) を Optional 内側だけ取り出す
  if (Array.isArray(def.anyOf)) {
    const nonNull = def.anyOf.filter((d) => d?.type !== "null");
    if (nonNull.length === 1) return categorizeType(nonNull[0]);
  }
  if (def.type === "array") {
    const inner = def.items ? categorizeType(def.items) : "unknown";
    return `array<${inner}>`;
  }
  if (typeof def.type === "string") return def.type;
  return "unknown";
}

function diff(bffTypes, agentTypes) {
  const allKeys = new Set([
    ...Object.keys(bffTypes),
    ...Object.keys(agentTypes),
  ]);
  const issues = [];
  for (const key of allKeys) {
    if (!(key in bffTypes)) {
      issues.push(`  + ai-agent only: ${key} (${agentTypes[key]})`);
      continue;
    }
    if (!(key in agentTypes)) {
      issues.push(`  + BFF only: ${key} (${bffTypes[key]})`);
      continue;
    }
    if (bffTypes[key] !== agentTypes[key]) {
      issues.push(
        `  ~ type mismatch: ${key} — BFF: ${bffTypes[key]} / ai-agent: ${agentTypes[key]}`,
      );
    }
  }
  return issues;
}

// ── ai-agent schema fetch ───────────────────────────────────────────────────

function fetchAiAgentSchemas() {
  const script = resolve(
    REPO_ROOT,
    "cicd/scripts/testing/export-ai-agent-schemas.py",
  );
  const out = execFileSync(
    "uv",
    [
      "run",
      "--directory",
      "apps/services/ai-agent",
      "python",
      script,
    ],
    { cwd: REPO_ROOT, encoding: "utf-8", stdio: ["ignore", "pipe", "inherit"] },
  );
  return JSON.parse(out);
}

// ── main ────────────────────────────────────────────────────────────────────

/** stdout への成功系出力 (CLI ツールとしての結果表示。debug 用 console.log ではない) */
function out(line) {
  process.stdout.write(`${line}\n`);
}

function main() {
  out("[L0 contract] BFF zod ↔ ai-agent pydantic 比較を開始");

  const agentSchemas = fetchAiAgentSchemas();

  let totalIssues = 0;
  for (const [name, zodSchema] of Object.entries(BFF_SCHEMAS)) {
    const agent = agentSchemas[name];
    if (!agent) {
      console.error(`[FAIL] ai-agent 側に schema ${name} が見つからない`);
      totalIssues++;
      continue;
    }
    const bffJson = zodToJsonSchema(zodSchema, name);
    // zodToJsonSchema は { definitions: { [name]: schema } } を返す
    const bffSchema = bffJson?.definitions?.[name] ?? bffJson;
    const bffTypes = extractFieldTypes(bffSchema);
    const agentTypes = extractFieldTypes(agent);
    const issues = diff(bffTypes, agentTypes);

    if (issues.length === 0) {
      out(`  ✓ ${name}: フィールド集合 ${Object.keys(bffTypes).length} 件 一致`);
    } else {
      console.error(`  ✗ ${name}: 不整合 ${issues.length} 件`);
      for (const i of issues) console.error(i);
      totalIssues += issues.length;
    }
  }

  if (totalIssues > 0) {
    console.error(
      `\n[L0 contract] ${totalIssues} 件の不整合を検出。BFF zod or ai-agent pydantic のどちらか一方の変更が漏れている可能性があります。`,
    );
    process.exit(1);
  }
  out("\n[L0 contract] OK — 全契約一致");
}

main();
