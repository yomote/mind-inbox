/**
 * tRPC エラーの **機械可読な識別子**。BFF とフロントが同じ 1 個のリテラルを見る。
 *
 * なぜ code だけでは足りないか (#82 / PR #416 Codex 4 巡目 P2):
 * tRPC は **procedure が見つからないときにも `NOT_FOUND`** を返す
 * (`No procedure found on path "consultation.approve"`)。つまり frontend / BFF の
 * 版ずれ・BFF 単独配備・ルーティング事故でも `NOT_FOUND` は飛んでくる。
 * `code === "NOT_FOUND"` だけで「承認レコードがもう無い」と読むと、**生きている
 * checkpoint を持つ承認カードを閉じてしまう** (承認も却下も再試行もできなくなる) —
 * ai-agent の 404 を detail で選り分けたのと同じ事故が、1 段上の層で起きる。
 *
 * だから BFF は「承認レコードがもう無い」ときだけこの token を `message` に載せ、
 * フロントは **code + この token の一致**で判定する。
 *
 * このファイルは **依存を持たない値だけ**を置く (フロントが値として import するため。
 * zod や Node の API を足すとフロントのバンドルに引きずり込まれる)。
 */

/**
 * `consultation.approve` の対象が ai-agent 側にもう無い (TTL 1h 失効 / 再起動で消えた /
 * approved・rejected 済み)。**再試行しても永久に成功しない**ので、フロントはカードを
 * 閉じて会話を進められる状態に戻す (dialogue-session.mdx §5.9)。
 *
 * 逆に、この token が付いていない `NOT_FOUND` は「承認レコードについて何も言っていない」
 * ので通信失敗と同じ扱い = カードを残す。
 */
export const APPROVAL_NOT_FOUND_TOKEN = "approval-not-found";

/**
 * 抽出 (`consultation.preview` / `consultation.extract`) の失敗理由 (#183)。
 * BFF はこの token を `TRPCError.message` に載せ、フロントは token から復帰導線を選ぶ。
 *
 * ここに 1 個だけ置く理由は上と同じ (PR #416 judge minor-2): 以前は BFF の
 * `aiAgentClient.ts` (union 型) と フロントの `problems.ts` (照合用の配列) に**同じ
 * リテラルが書き写されていた**。BFF 側だけ改名すると、フロントの照合が外れて
 * **原因別の案内が黙って「unknown」(汎用エラー) に落ちる** — 画面は壊れないので
 * 気づけない。値を共有すれば、片側だけ変える書き方自体ができなくなる。
 *
 * `as const` にしているのは、この配列から型を導けるようにするため
 * (メンバを増減すると BFF の分岐が型エラーになる = 追随漏れが機械で出る)。
 */
export const EXTRACT_FAILURE_TOKENS = [
  /** 会話が手に入らない (ai-agent の 404)。会話を送り直せば解消する。 */
  "session-missing",
  /** LLM の応答を解釈できなかった (502)。「抽出 0 件」とは別物。 */
  "llm-parse-failed",
  /** それ以外の上流失敗 (5xx / ネットワーク断)。再試行で直りうる。 */
  "upstream-failed",
] as const;

/** 抽出の失敗理由 (BFF が返しうる token)。フロントはこれに `unknown` を足して扱う。 */
export type ExtractFailureToken = (typeof EXTRACT_FAILURE_TOKENS)[number];
