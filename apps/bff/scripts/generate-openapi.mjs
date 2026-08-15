// @ts-check
/**
 * BFF tRPC router (zod schema) → OpenAPI YAML を生成する。
 *
 * 真実は `apps/bff/src/trpc/router.ts` の zod (`.input()` / `.output()`)。
 * このスクリプトはそれを introspection して `docs/api/bff-trpc.yaml` を**派生生成**する。
 * 生成物は commit され、CI で再生成 → `git diff --exit-code` で実装との乖離を検知する (#8)。
 *
 * なぜ trpc-to-openapi を使わないか:
 *   このBFFは単一 tRPC エントリポイント (`/api/trpc/{path}`) で、REST エンドポイントを公開しない
 *   (ADR 0001)。trpc-to-openapi は各 procedure に REST の method/path メタを強制し実態と乖離するため、
 *   router を直接 introspection して「1 procedure = 1 operation」で素直に表現する。
 *
 * 表現の約束:
 *   - query  → GET  /api/trpc/{path} (input があれば `?input=<json>` クエリパラメータ)
 *   - mutation → POST /api/trpc/{path} (input を requestBody に置く)
 *   - レスポンスは `.output()` の zod schema。tRPC のバッチ/superjson ラッピングは表現しない
 *     (目的は zod 変更の可視化と diff 検知であり、ワイヤ形式の厳密再現ではない)。
 */

import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stringify } from "yaml";
import { z } from "zod";

import { appRouter } from "../dist/src/trpc/router.js";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "../../..");
const OUT_PATH = resolve(REPO_ROOT, "docs/api/bff-trpc.yaml");
const TRPC_BASE = "/api/trpc";

/** @type {{ version: string }} */
const pkg = require("../package.json");

/**
 * zod schema → OpenAPI 3.0 互換 JSON Schema (自己完結・$ref なし)。
 * zod v4 移行 (#449) で zod-to-json-schema から本体の `z.toJSONSchema` に置き換えた。
 * `reused: "inline"` は旧 `$refStrategy: "none"` 相当 (再利用スキーマも $ref を作らず展開)。
 *
 * `io` は方向で使い分ける: リクエスト (`.input()`) は "input" — `.default()` 付き
 * フィールドはクライアントが省略できるので required に入れない。レスポンス
 * (`.output()`) は "output" — BFF は parse 時に default を埋めてから返すので必ず在る。
 * 全部 "output" (zod の既定) にすると、省略可のリクエストフィールドが required と
 * 書かれる嘘の仕様書になる。
 */
function toSchema(zodSchema, io) {
  return z.toJSONSchema(zodSchema, {
    target: "openapi-3.0",
    reused: "inline",
    io,
  });
}

function buildOperation(path, def) {
  const [tag] = path.split(".");
  const inputSchema = def.inputs?.length ? toSchema(def.inputs[0], "input") : null;
  const outputSchema = def.output ? toSchema(def.output, "output") : null;

  /** @type {Record<string, unknown>} */
  const operation = {
    operationId: path,
    tags: [tag],
    summary: path,
    responses: {
      200: {
        description: "OK",
        ...(outputSchema
          ? { content: { "application/json": { schema: outputSchema } } }
          : {}),
      },
    },
  };

  const method = def.type === "query" ? "get" : "post";

  if (inputSchema) {
    if (method === "get") {
      // tRPC の query は `?input=<url-encoded JSON>` で渡す
      operation.parameters = [
        {
          name: "input",
          in: "query",
          required: true,
          description: "tRPC input (URL-encoded JSON)",
          schema: inputSchema,
        },
      ];
    } else {
      operation.requestBody = {
        required: true,
        content: { "application/json": { schema: inputSchema } },
      };
    }
  }

  return { method, operation };
}

function generate() {
  const procedures = appRouter._def.procedures;

  /** @type {Record<string, Record<string, unknown>>} */
  const paths = {};
  // procedure 名でソートして出力順を安定させる (diff を最小化)
  const sortedPaths = Object.keys(procedures).sort();

  for (const path of sortedPaths) {
    const def = procedures[path]._def;
    const { method, operation } = buildOperation(path, def);
    paths[`${TRPC_BASE}/${path}`] = { [method]: operation };
  }

  const doc = {
    openapi: "3.0.3",
    info: {
      title: "Mind Inbox BFF (tRPC)",
      version: pkg.version,
      description:
        "**自動生成 — 手書き編集禁止**。真実は apps/bff/src/trpc/router.ts の zod schema。\n" +
        "再生成: `npm run docs:openapi`。BFF は単一 tRPC エントリポイント " +
        "(/api/trpc/{path}) で、ここでは 1 procedure = 1 operation として表現している (ADR 0001)。",
    },
    paths,
  };

  // YAML serialize。プロパティ順を保つため `sortMapEntries: false`。
  const yaml = stringify(doc, { sortMapEntries: false, lineWidth: 0 });
  writeFileSync(OUT_PATH, yaml, "utf-8");
  process.stdout.write(
    `[docs:openapi:bff] ${Object.keys(paths).length} operations → ${OUT_PATH}\n`,
  );
}

generate();
