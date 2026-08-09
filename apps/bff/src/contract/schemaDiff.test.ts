/**
 * [L0] 契約比較ロジック (schemaDiff) 自身のテスト。
 *
 * 無いと何が静かに通るか: contract-check.mjs が「全契約一致」と出しても、比較器が
 * ネストを見ていない / required も enum も見ていない、という状態に戻せてしまう。
 * つまり **契約テストが何も検出しない張りぼてになったこと** に誰も気づけない。
 * そのため各ケースは「わざと不整合なペアを食わせて検出できること」を主眼に置く。
 *
 * ここで test しないこと:
 *   - 実際の BFF zod / ai-agent pydantic の中身 (それは npm run test:contract が実物で見る)
 *   - JSON Schema 仕様の網羅 (使っている表現だけを固定する)
 */

import { describe, expect, it } from "vitest";
import {
  diffFieldMaps,
  diffSchemas,
  flattenSchema,
  normalizeKey,
  type JsonSchemaNode,
} from "./schemaDiff";

/** zod-to-json-schema 風 (camelCase / `type: [T,"null"]` / 全部インライン) */
const zodStyle: JsonSchemaNode = {
  type: "object",
  properties: {
    sessionId: { type: "string" },
    items: {
      type: "array",
      items: {
        type: "object",
        properties: {
          mention: {
            type: "object",
            properties: {
              dumpId: { type: ["string", "null"] },
              affect: {
                type: "object",
                properties: {
                  label: { type: "string" },
                  valence: { type: "string", enum: ["negative", "neutral", "positive"] },
                  intensity: { type: "number", minimum: 0, maximum: 1 },
                },
                required: ["label", "valence", "intensity"],
                additionalProperties: false,
              },
              proposedTags: { type: "array", items: { type: "string" } },
            },
            required: ["dumpId", "affect", "proposedTags"],
            additionalProperties: false,
          },
        },
        required: ["mention"],
        additionalProperties: false,
      },
    },
    newProblemCount: { type: "integer", minimum: 0 },
  },
  required: ["sessionId", "items", "newProblemCount"],
  additionalProperties: false,
};

/** pydantic 風 (snake_case / `$defs` + `$ref` / `anyOf` + null / `title` 付き) */
const pydanticStyle: JsonSchemaNode = {
  $defs: {
    Affect: {
      title: "Affect",
      type: "object",
      properties: {
        label: { title: "Label", type: "string" },
        valence: { title: "Valence", type: "string", enum: ["negative", "neutral", "positive"] },
        intensity: { title: "Intensity", type: "number", minimum: 0, maximum: 1 },
      },
      required: ["label", "valence", "intensity"],
    },
    ExtractedItem: {
      title: "ExtractedItem",
      type: "object",
      properties: { mention: { $ref: "#/$defs/Mention" } },
      required: ["mention"],
    },
    Mention: {
      title: "Mention",
      type: "object",
      properties: {
        dumpId: { anyOf: [{ type: "string" }, { type: "null" }], title: "Dumpid" },
        affect: { $ref: "#/$defs/Affect" },
        proposedTags: { type: "array", items: { type: "string" }, title: "Proposedtags" },
      },
      required: ["dumpId", "affect", "proposedTags"],
    },
  },
  title: "ExtractionResult",
  type: "object",
  properties: {
    session_id: { title: "Session Id", type: "string" },
    items: { type: "array", items: { $ref: "#/$defs/ExtractedItem" }, default: [] },
    new_problem_count: { title: "New Problem Count", type: "integer", minimum: 0 },
  },
  required: ["session_id", "new_problem_count"],
};

/** deep clone してから 1 箇所だけ書き換えるヘルパ (壊し方を 1 行で読めるようにする) */
function mutate(base: JsonSchemaNode, apply: (draft: never) => void): JsonSchemaNode {
  const clone = structuredClone(base);
  apply(clone as never);
  return clone;
}

describe("[L0] normalizeKey", () => {
  // 無いと何が静かに通るか: camelCase ↔ snake_case の正規化が壊れると全フィールドが
  // 「片側にしか無い」と誤検出され、契約テストが常に赤 = 無視される存在になる。
  it("camelCase を snake_case に寄せる", () => {
    expect(normalizeKey("newProblemCount")).toBe("new_problem_count");
    expect(normalizeKey("session_id")).toBe("session_id");
    expect(normalizeKey("id")).toBe("id");
  });
});

describe("[L0] flattenSchema — 再帰展開", () => {
  // 無いと何が静かに通るか: トップレベルしか見ない比較器に戻ると、Mention の中身が
  // 片側だけ変わっても「フィールド集合一致」で緑になる (#59 の元凶そのもの)。
  it("ネスト・配列要素まで一意なパスに展開する", () => {
    const paths = Object.keys(flattenSchema(zodStyle)).sort();
    expect(paths).toEqual([
      "items",
      "items[]",
      "items[].mention",
      "items[].mention.affect",
      "items[].mention.affect.intensity",
      "items[].mention.affect.label",
      "items[].mention.affect.valence",
      "items[].mention.dump_id",
      "items[].mention.proposed_tags",
      "items[].mention.proposed_tags[]",
      "new_problem_count",
      "session_id",
    ]);
  });

  // 無いと何が静かに通るか: $ref を解決しないと pydantic 側は "object" 止まりになり、
  // $defs の中身 (= 実際の契約) が丸ごと比較対象から抜け落ちる。
  it("$defs / $ref を解決して BFF 側と同じパス集合になる", () => {
    expect(Object.keys(flattenSchema(pydanticStyle)).sort()).toEqual(
      Object.keys(flattenSchema(zodStyle)).sort(),
    );
  });

  // 無いと何が静かに通るか: array の要素型を見ないと array<string> → array<number> が通る。
  it("配列要素の型を `path[]` として持つ", () => {
    expect(flattenSchema(zodStyle)["items[].mention.proposed_tags[]"]).toEqual({
      type: "string",
      nullable: false,
      optional: false,
    });
  });
});

describe("[L0] diffSchemas — 表層差は吸収する (false positive を出さない)", () => {
  // 無いと何が静かに通るか: 表現差で毎回赤くなる契約テストは「また誤検出だ」と
  // 無視されるようになり、本物の drift も一緒に素通りする。
  it("camelCase↔snake_case / anyOf+null↔type:[T,null] / $ref / title・minimum の差では落ちない", () => {
    const { issues, fieldCount } = diffSchemas(zodStyle, pydanticStyle);
    expect(issues).toEqual([]);
    expect(fieldCount).toBe(12);
  });

  // 無いと何が静かに通るか: pydantic の「default があるので required ではない」を
  // そのまま optional 扱いすると、常に埋まるフィールド (items 等) が毎回赤くなる。
  it("default を持つフィールドは required でなくても optional 扱いしない", () => {
    // pydanticStyle.items は required に無いが default: [] を持つ (= 欠落しない)
    expect(flattenSchema(pydanticStyle)["items"]?.optional).toBe(false);
  });
});

describe("[L0] diffSchemas — わざと壊すと検出する", () => {
  // 無いと何が静かに通るか: ネストの奥の型変更 (string → integer) が黙って通り、
  // BFF は文字列を期待しているのに ai-agent が数値を返す本番事故になる。
  it("ネストの奥の型が食い違うと 1 行で報告する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Affect.properties.label.type = "integer";
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ type mismatch: items[].mention.affect.label — BFF: string / ai-agent: integer",
    ]);
  });

  // 無いと何が静かに通るか: 片側だけフィールドが増減しても気づけない。
  it("ネストの奥に片側だけフィールドが増えると検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.properties.confidence = { anyOf: [{ type: "number" }, { type: "null" }] };
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.required.push("confidence");
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  + ai-agent only: items[].mention.confidence (number, nullable)",
    ]);
  });

  // 無いと何が静かに通るか: 片側だけ必須↔任意が入れ替わると、送り手が送らなくなった
  // フィールドを受け手が必須として読み、実行時に落ちる。
  it("片側だけ optional 化すると検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // required から外す (default も無いので「欠落しうる」)
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.required = ["affect", "proposedTags"];
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ required mismatch: items[].mention.dump_id — BFF: required / ai-agent: optional",
    ]);
  });

  // 無いと何が静かに通るか: Theme の 8 分類 / Literal の値が片側だけ増減しても緑になり、
  // 未知の enum 値が飛んで受け手が分岐を落とす。
  it("enum メンバの増減を「どちら側にしか無いか」つきで検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Affect.properties.valence.enum = ["negative", "positive", "mixed"];
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ enum mismatch: items[].mention.affect.valence — BFF only: [neutral] / ai-agent only: [mixed]",
    ]);
  });

  // 無いと何が静かに通るか: 片側だけ enum 制約を外すと、任意の文字列が通るようになったことに
  // 気づけない (「string 同士だから一致」で緑)。
  it("片側だけ enum 制約が消えると検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      delete d.$defs.Affect.properties.valence.enum;
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ enum mismatch: items[].mention.affect.valence — BFF: [negative | neutral | positive] / ai-agent: 制約なし",
    ]);
  });

  // 無いと何が静かに通るか: 片側だけ nullable を外す/付けると、受け手が null で落ちる
  // (逆に「null を返さなくなった」設計変更にも気づけない)。
  it("nullable の食い違いを検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.properties.dumpId = { type: "string" };
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ nullable mismatch: items[].mention.dump_id — BFF: nullable / ai-agent: non-null",
    ]);
  });

  // 無いと何が静かに通るか: 配列の要素型だけ差し替わっても「どちらも array」で緑になる。
  it("配列要素の型変更 (array<string> → array<number>) を検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.properties.proposedTags.items = { type: "number" };
    });
    expect(diffSchemas(zodStyle, broken).issues).toEqual([
      "  ~ type mismatch: items[].mention.proposed_tags[] — BFF: string / ai-agent: number",
    ]);
  });

  // 無いと何が静かに通るか: object ↔ array の入れ替えは wire 形が全く違うのに、
  // 子フィールドの差分に埋もれて原因が読めなくなる (1 行目で分かる必要がある)。
  it("object → array のようなカテゴリ変更を検出する", () => {
    const broken = mutate(pydanticStyle, (d: JsonSchemaNode) => {
      // @ts-expect-error テスト用に既知の形へ踏み込む
      d.$defs.Mention.properties.affect = { type: "array", items: { type: "string" } };
    });
    const issues = diffSchemas(zodStyle, broken).issues;
    expect(issues[0]).toBe(
      "  ~ type mismatch: items[].mention.affect — BFF: object / ai-agent: array",
    );
  });
});

describe("[L0] diffFieldMaps — 出力の形", () => {
  // 無いと何が静かに通るか: 出力がパスを含まないと、失敗しても「どこが違うのか」を
  // 人が探す羽目になり、契約テストの価値 (失敗の局所化) が消える。
  it("片側にしか無いフィールドを BFF / ai-agent で区別して出す", () => {
    const issues = diffFieldMaps(
      { a: { type: "string", nullable: false, optional: false } },
      { b: { type: "integer", nullable: false, optional: true } },
    );
    expect(issues).toEqual([
      "  + BFF only: a (string)",
      "  + ai-agent only: b (integer, optional)",
    ]);
  });
});
