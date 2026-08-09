/**
 * [L0] JSON Schema 同士を「wire 契約」の粒度で突き合わせる純粋ロジック。
 *
 * 利用者は `apps/bff/scripts/contract-check.mjs` (`npm run test:contract`) のみ。
 * ランタイム (Functions) からは import しない — テストハーネス用のモジュールとして
 * `src/` に置いているのは、eslint / tsc / vitest の既存ハーネスに素直に載せるため。
 *
 * 何を比較するか (= 何が壊れたら落ちてほしいか):
 *   1. フィールドパス集合   — ネストも含めて片側にしか無いフィールドを検出
 *   2. 型カテゴリ           — string ↔ integer / object ↔ array のような危険な入れ替わり
 *   3. null 許容            — 片側だけ nullable を外す / 付ける (受け手が null で落ちる)
 *   4. 欠落可能性 (optional) — 片側だけ「省略可」にする (送り手が送らないのに受け手が必須)
 *   5. enum メンバ集合      — Theme の 8 分類 / Literal の値が片側だけ増減する
 *
 * 何を比較しないか (意図的に見ない = false positive を出さないため):
 *   - 数値の min/max、文字列長、description/title などの注釈
 *   - `additionalProperties` の有無 (zod は false を出し pydantic は出さない)
 *   - default の値そのもの (「default を持つか」だけ見る。後述)
 *
 * 表層差の吸収:
 *   - camelCase ↔ snake_case  … `normalizeKey()` でパスの各セグメントを snake_case へ寄せる
 *   - `Optional[T]` (anyOf + null) ↔ `z.nullable()` (`type: ["T","null"]`)
 *     … どちらも「T かつ nullable」に潰す
 *   - `const` (単一 Literal) ↔ `enum` … どちらも 1 要素の enum 集合として扱う
 *   - `allOf` 1 要素ラップ (`{"allOf":[{"$ref":...}], "default":...}`)
 *     … pydantic が「`$ref` を持つフィールドに default / default_factory が付いた」ときに出す形。
 *       中身 1 つ + 注釈 (`default` / `title` / `description` など) だけなら中身に解決する。
 *       このとき **`default` は中身側へ引き継ぐ** (落とすと optional 判定が反転する)。
 *       実体のある schema が 2 つ以上ある「本物の交差型」は畳まない (スコープ外 = "unknown")
 *
 * 「optional」の定義について:
 *   JSON Schema の `required` は「送り手が省略できるか」しか表さない。pydantic は
 *   default / default_factory を持つフィールドを `required` から外すが、その場合でも
 *   受け手が default で埋めるので契約は壊れない。zod の `.default()` も同じ。
 *   よって **「required でない かつ default も無い」= 欠落しうる (optional)** と定義する。
 *   これで「片側だけ `.optional()` を付けた」= 送り手が黙って送らなくなる変更だけが落ちる。
 *   (pydantic 側の default_factory は JSON Schema に `default` が出ないので、
 *    export-ai-agent-schemas.py 側で `default` として書き出している)
 */

/** JSON Schema ノード。外部生成物なので緩く受ける。 */
export type JsonSchemaNode = Record<string, unknown>;

export type FieldShape = {
  /** 型カテゴリ ("string" / "integer" / "number" / "boolean" / "array" / "object" / "unknown" / "union<...>") */
  type: string;
  /** null を取りうるか */
  nullable: boolean;
  /** payload から欠落しうるか (required でなく default も無い) */
  optional: boolean;
  /** enum / const のメンバ集合 (ソート済み)。enum でなければ undefined */
  enumValues?: string[];
};

/** フィールドパス (`items[].mention.affect.valence`) → 形状 */
export type FieldMap = Record<string, FieldShape>;

/** 再帰の暴走 (相互参照する $ref など) を止める上限。実データは深さ 5 程度。 */
const MAX_DEPTH = 16;

/** camelCase → snake_case (機械的)。パスの 1 セグメントに対して適用する。 */
export function normalizeKey(key: string): string {
  return key.replace(/([A-Z])/g, "_$1").toLowerCase();
}

function isObject(value: unknown): value is JsonSchemaNode {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * `allOf` ラッパの兄弟として許す注釈キーワード。
 * これ以外 (`properties` / `type` / `required` など) が並んでいたら「ただのラッパ」ではないので畳まない。
 */
const ANNOTATION_KEYWORDS = new Set([
  "default",
  "title",
  "description",
  "examples",
  "deprecated",
  "readOnly",
  "writeOnly",
  "$comment",
]);

/**
 * `{"allOf":[{...}], "default":..., "title":...}` を中身 1 枚に畳む (畳めなければ node をそのまま返す)。
 * pydantic が `$ref` フィールドに default / default_factory を付けたときに出す表層差の吸収。
 * 兄弟の注釈 (とくに `default`) は中身へ引き継ぐ — 落とすと「default 無し = optional」に転んで
 * 逆向きの false positive になる。
 * `allOf` に実体が 2 つ以上ある「本物の交差型」は今回のスコープ外なので畳まない。
 */
export function unwrapAllOf(node: JsonSchemaNode): JsonSchemaNode {
  const allOf = node["allOf"];
  if (!Array.isArray(allOf) || allOf.length !== 1) return node;
  const inner = allOf[0];
  if (!isObject(inner)) return node;
  const siblings = Object.keys(node).filter((key) => key !== "allOf");
  if (!siblings.every((key) => ANNOTATION_KEYWORDS.has(key))) return node;

  const merged: JsonSchemaNode = { ...inner };
  for (const key of siblings) merged[key] = node[key];
  return merged;
}

/**
 * `#/$defs/Foo` `#/definitions/Foo/properties/bar` のような同一ドキュメント内 JSON Pointer と、
 * `allOf` 1 要素ラップを解決して「実体の schema」まで辿る。
 * 外部参照 (`http://...`) は解決できないのでそのまま返す (型カテゴリ "unknown" に落ちる)。
 *
 * 途中で見つけた `default` は戻り値に残す。`$ref` の解決先 (`$defs` の共有定義) は default を
 * 持たないので、引き継がないとフィールド側に書かれた default が消えてしまう。
 */
export function resolveRef(node: JsonSchemaNode, root: JsonSchemaNode): JsonSchemaNode {
  let current = node;
  let hasDefault = "default" in node;
  let defaultValue = node["default"];

  for (let hops = 0; hops < MAX_DEPTH; hops++) {
    const unwrapped = unwrapAllOf(current);
    if (unwrapped !== current) {
      current = unwrapped;
      if ("default" in current) {
        hasDefault = true;
        defaultValue = current["default"];
      }
      continue;
    }

    const ref = current["$ref"];
    if (typeof ref !== "string" || !ref.startsWith("#")) break;
    const segments = ref
      .slice(1)
      .split("/")
      .filter((s) => s.length > 0)
      .map((s) => s.replace(/~1/g, "/").replace(/~0/g, "~"));
    let target: unknown = root;
    for (const segment of segments) {
      if (!isObject(target)) return withDefault(current, hasDefault, defaultValue);
      target = target[segment];
    }
    if (!isObject(target)) return withDefault(current, hasDefault, defaultValue);
    current = target;
    if ("default" in current) {
      hasDefault = true;
      defaultValue = current["default"];
    }
  }
  return withDefault(current, hasDefault, defaultValue);
}

function withDefault(node: JsonSchemaNode, hasDefault: boolean, value: unknown): JsonSchemaNode {
  if (!hasDefault || "default" in node) return node;
  return { ...node, default: value };
}

/**
 * nullable 表現 (`anyOf` + null / `type: [T,"null"]` / `oneOf`) を剥がして
 * 「実体の schema」と「null を取りうるか」に分ける。
 */
export function unwrapNullable(
  node: JsonSchemaNode,
  root: JsonSchemaNode,
): { schema: JsonSchemaNode; nullable: boolean } {
  // type: ["string", "null"] 形式 (zod-to-json-schema の .nullable())
  if (Array.isArray(node["type"])) {
    const types = node["type"].filter((t): t is string => typeof t === "string");
    const nonNull = types.filter((t) => t !== "null");
    if (types.length !== nonNull.length) {
      return {
        schema: { ...node, type: nonNull.length === 1 ? nonNull[0] : nonNull },
        nullable: true,
      };
    }
    return { schema: node, nullable: false };
  }

  // anyOf / oneOf 形式 (pydantic の Optional[T] / zod の .nullable() on 制約付き型)
  const union = node["anyOf"] ?? node["oneOf"];
  if (Array.isArray(union)) {
    const members = union.filter(isObject).map((m) => resolveRef(m, root));
    const nonNull = members.filter((m) => m["type"] !== "null");
    const nullable = members.length !== nonNull.length;
    if (nonNull.length === 1) {
      const inner = unwrapNullable(nonNull[0], root);
      return { schema: inner.schema, nullable: nullable || inner.nullable };
    }
    if (nonNull.length === 0) return { schema: { type: "null" }, nullable: true };
    return { schema: { ...node, anyOf: nonNull, oneOf: undefined }, nullable };
  }

  return { schema: node, nullable: false };
}

/** 型カテゴリを粗く求める。粒度を粗く保つのは注釈差で落とさないため。 */
export function categorizeType(node: JsonSchemaNode): string {
  const type = node["type"];
  if (typeof type === "string") return type;
  if (Array.isArray(node["anyOf"])) {
    const inner = node["anyOf"]
      .filter(isObject)
      .map((m) => categorizeType(m))
      .sort();
    return `union<${inner.join("|")}>`;
  }
  if (isObject(node["properties"])) return "object";
  if (node["enum"] !== undefined || node["const"] !== undefined) return "string";
  // allOf 1 要素ラップ (中身がインライン schema の場合)。$ref 入りは resolveRef が先に畳んでいる。
  const unwrapped = unwrapAllOf(node);
  if (unwrapped !== node) return categorizeType(unwrapped);
  return "unknown";
}

/** enum / const のメンバ集合を取り出す (ソート済み)。enum でなければ undefined。 */
export function extractEnum(node: JsonSchemaNode): string[] | undefined {
  if (Array.isArray(node["enum"])) {
    return node["enum"].map((v) => String(v)).sort();
  }
  if (node["const"] !== undefined) return [String(node["const"])];
  return undefined;
}

/**
 * JSON Schema を「フィールドパス → 形状」のフラットな map に展開する。
 * 配列は要素を `path[]` として展開するので `array<string> → array<number>` も捕まる。
 */
export function flattenSchema(schema: JsonSchemaNode, root?: JsonSchemaNode): FieldMap {
  const document = root ?? schema;
  const out: FieldMap = {};
  const { schema: rootObject } = unwrapNullable(resolveRef(schema, document), document);
  visitObject(rootObject, document, "", out, 0);
  return out;
}

function visitObject(
  node: JsonSchemaNode,
  root: JsonSchemaNode,
  prefix: string,
  out: FieldMap,
  depth: number,
): void {
  if (depth > MAX_DEPTH) return;
  const properties = node["properties"];
  if (!isObject(properties)) return;
  const required = new Set(
    Array.isArray(node["required"])
      ? node["required"].filter((r): r is string => typeof r === "string").map(normalizeKey)
      : [],
  );

  for (const [rawName, rawDef] of Object.entries(properties)) {
    if (!isObject(rawDef)) continue;
    const name = normalizeKey(rawName);
    const path = prefix ? `${prefix}.${name}` : name;
    const resolved = resolveRef(rawDef, root);
    // default は $ref を辿る前 (フィールド側) にも付きうる
    const hasDefault = "default" in rawDef || "default" in resolved;
    const optional = !required.has(name) && !hasDefault;
    describeField(resolved, root, path, out, depth, optional);
  }
}

function describeField(
  resolved: JsonSchemaNode,
  root: JsonSchemaNode,
  path: string,
  out: FieldMap,
  depth: number,
  optional: boolean,
): void {
  const { schema: base, nullable } = unwrapNullable(resolved, root);
  const type = categorizeType(base);
  const enumValues = extractEnum(base);

  out[path] = enumValues ? { type, nullable, optional, enumValues } : { type, nullable, optional };

  if (type === "array") {
    const items = base["items"];
    if (isObject(items)) {
      // 要素は「欠落しうる」概念を持たない (配列が空なだけ) ので optional=false 固定
      describeField(resolveRef(items, root), root, `${path}[]`, out, depth + 1, false);
    }
    return;
  }
  if (type === "object") {
    visitObject(base, root, path, out, depth + 1);
  }
}

// ── diff ─────────────────────────────────────────────────────────────────────

function formatShape(shape: FieldShape): string {
  const parts = [shape.type];
  if (shape.nullable) parts.push("nullable");
  if (shape.optional) parts.push("optional");
  if (shape.enumValues) parts.push(`enum(${shape.enumValues.length})`);
  return parts.join(", ");
}

function formatEnum(values: string[] | undefined): string {
  if (!values) return "制約なし";
  if (values.length === 0) return "(なし)";
  return `[${values.join(" | ")}]`;
}

/**
 * 2 つの FieldMap を突き合わせて、不整合を 1 件 1 行の文字列で返す。
 * 出力は「どのパスが / BFF 側は何で / ai-agent 側は何か」が 1 行で読めることを最優先にする。
 */
export function diffFieldMaps(bff: FieldMap, agent: FieldMap): string[] {
  const paths = [...new Set([...Object.keys(bff), ...Object.keys(agent)])].sort();
  const issues: string[] = [];

  for (const path of paths) {
    const b = bff[path];
    const a = agent[path];

    if (!b) {
      issues.push(`  + ai-agent only: ${path} (${formatShape(a)})`);
      continue;
    }
    if (!a) {
      issues.push(`  + BFF only: ${path} (${formatShape(b)})`);
      continue;
    }
    if (b.type !== a.type) {
      issues.push(`  ~ type mismatch: ${path} — BFF: ${b.type} / ai-agent: ${a.type}`);
    }
    if (b.nullable !== a.nullable) {
      issues.push(
        `  ~ nullable mismatch: ${path} — BFF: ${b.nullable ? "nullable" : "non-null"} / ai-agent: ${
          a.nullable ? "nullable" : "non-null"
        }`,
      );
    }
    if (b.optional !== a.optional) {
      issues.push(
        `  ~ required mismatch: ${path} — BFF: ${b.optional ? "optional" : "required"} / ai-agent: ${
          a.optional ? "optional" : "required"
        }`,
      );
    }
    const enumIssue = diffEnum(path, b.enumValues, a.enumValues);
    if (enumIssue) issues.push(enumIssue);
  }

  return issues;
}

function diffEnum(
  path: string,
  bffEnum: string[] | undefined,
  agentEnum: string[] | undefined,
): string | null {
  if (!bffEnum && !agentEnum) return null;
  if (!bffEnum || !agentEnum) {
    return `  ~ enum mismatch: ${path} — BFF: ${formatEnum(bffEnum)} / ai-agent: ${formatEnum(agentEnum)}`;
  }
  const bffOnly = bffEnum.filter((v) => !agentEnum.includes(v));
  const agentOnly = agentEnum.filter((v) => !bffEnum.includes(v));
  if (bffOnly.length === 0 && agentOnly.length === 0) return null;
  return `  ~ enum mismatch: ${path} — BFF only: ${formatEnum(bffOnly)} / ai-agent only: ${formatEnum(agentOnly)}`;
}

/** schema 1 組を比較する。呼び出し側 (contract-check.mjs) の入口。 */
export function diffSchemas(
  bffSchema: JsonSchemaNode,
  agentSchema: JsonSchemaNode,
): { issues: string[]; fieldCount: number } {
  const bffMap = flattenSchema(bffSchema);
  const agentMap = flattenSchema(agentSchema);
  return {
    issues: diffFieldMaps(bffMap, agentMap),
    fieldCount: Object.keys(bffMap).length,
  };
}
