/**
 * `POST /api/tts` の呼び分けの判定。**依存ゼロ**にしてあるのは、Vite ビルドの外
 * (Playwright の L4 spec) からも同じ定義を import させるため — `http.ts` は
 * `import.meta.env` を触るので Node からは読めない。
 */

/** BFF `/api/tts` の呼び方。本合成だけが audio/wav を返す。 */
export type TtsCallKind = "synthesis" | "plan" | "prefetch";

/**
 * リクエストボディ (JSON 文字列) から呼び方を判定する。
 *
 * 変種は増える — #132 で `prefetch`、#185 で `plan` が足された。**「既知の変種を
 * 除外する」書き方は 2 回とも古くなり**、そのたびに L4 が別の呼び出しを掴んで
 * 「毎朝の実環境チェックが赤」になった (deploy 3 連敗 / golden-path-monitor 2 晩)。
 * 判定をここ 1 箇所に置き、L1 テストが「クライアント 3 種のうち本合成はちょうど 1 つ」
 * を固定する。
 */
export function ttsCallKind(rawBody: string | null): TtsCallKind | null {
  if (!rawBody) return null;
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(rawBody) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (body.plan) return "plan";
  if (body.prefetch) return "prefetch";
  return "synthesis";
}

/** 本合成 (audio/wav が返る呼び出し) か。L4 が待つのはこれだけ。 */
export function isTtsSynthesisBody(rawBody: string | null): boolean {
  return ttsCallKind(rawBody) === "synthesis";
}
