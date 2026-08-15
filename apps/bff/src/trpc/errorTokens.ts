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
 * このファイルは **依存を持たない値と、その値の符号化 / 復号だけ**を置く (フロントが
 * 値として import するため。zod や Node の API を足すとフロントのバンドルに
 * 引きずり込まれる)。
 */

/**
 * `consultation.approve` の対象が ai-agent 側にもう無い (TTL 1h 失効 / 再起動で消えた /
 * checkpoint が消えた)。**再試行しても永久に成功しない**ので、フロントはカードを
 * 閉じて会話を進められる状態に戻す (dialogue-session.mdx §5.9)。
 *
 * **すでに approved / rejected 済みはこの token ではない** (#82 / PO 裁定 2026-08-15
 * B 案) — それは `APPROVAL_ALREADY_PROCESSED_TOKEN` (CONFLICT) 側で、実行の有無まで
 * 言い切れる。ここに混ぜ戻すと「実行されたか分かりません」に逆戻りする。
 *
 * 逆に、この token が付いていない `NOT_FOUND` は「承認レコードについて何も言っていない」
 * ので通信失敗と同じ扱い = カードを残す。
 */
export const APPROVAL_NOT_FOUND_TOKEN = "approval-not-found";

/**
 * `consultation.approve` の対象が **すでに処理済み** (#82 / PO 裁定 2026-08-15 B 案)。
 *
 * `NOT_FOUND` (もう無い) と分けるのが本体。ai-agent が二重送信も 404 で返していた頃は
 * 「副作用が実行されたか」がどの層でも判定できず、UI は「実行されたか分かりません」と
 * しか言えなかった (送信済みのメールを再送させうる)。BFF は 409 を `CONFLICT` +
 * この token に写し、フロントは結果 (承認済み / 却下済み) まで言い切る。
 *
 * **code だけで判定させない**理由は `APPROVAL_NOT_FOUND_TOKEN` と同じ — `CONFLICT` は
 * 将来ほかの procedure でも使いうる汎用コードなので、token 一致まで見て初めて
 * 「この承認は処理済み」と読める。
 */
export const APPROVAL_ALREADY_PROCESSED_TOKEN = "approval-already-processed";

/** 承認の解決結果。`approved` = 副作用は実行された / `rejected` = 実行されていない。 */
export const APPROVAL_PROCESSED_STATUSES = ["approved", "rejected"] as const;
export type ApprovalProcessedStatus = (typeof APPROVAL_PROCESSED_STATUSES)[number];

/**
 * 処理済み token に結果を載せる符号化 (`approval-already-processed:approved`)。
 *
 * tRPC のエラーは文字列 1 本 (`message`) しか運べないが、フロントは
 * 「承認済み = 実行された」「却下済み = 実行されていない」を言い分ける必要がある。
 * **符号化と復号を同じファイルに置く**のは、片側だけ書式を変えたときに黙って
 * 判定が外れる (= 汎用エラー文言に落ちる) のを防ぐため — リテラルを 1 個だけ持つ
 * 流儀と同じ理由。
 */
export function encodeApprovalAlreadyProcessed(status: ApprovalProcessedStatus): string {
  return `${APPROVAL_ALREADY_PROCESSED_TOKEN}:${status}`;
}

/**
 * `encodeApprovalAlreadyProcessed` の逆。**この書式でなければ null** を返す。
 *
 * 不明な status を「承認済み」に丸めない: 丸めると、将来 ai-agent 側に状態が増えたとき
 * **実行されていない操作を「実行されました」と案内**しうる。null はフロント側で
 * 「処理済みとは断定しない」経路へ落ちる。
 */
export function parseApprovalAlreadyProcessed(message: string): ApprovalProcessedStatus | null {
  const [token, status] = message.split(":");
  if (token !== APPROVAL_ALREADY_PROCESSED_TOKEN) return null;
  return APPROVAL_PROCESSED_STATUSES.find((candidate) => candidate === status) ?? null;
}

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
