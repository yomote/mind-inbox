import { TRPCClientError } from "@trpc/client";
// BFF と共有する機械可読 token (依存を持たない値だけの module / ADR 0001 と同じ流儀で
// 「BFF が真実」。zod を跨ぐスキーマは共有しないが、リテラル 1 個の重複は避ける)。
import {
  APPROVAL_NOT_FOUND_TOKEN,
  parseApprovalAlreadyProcessed,
} from "../../../bff/src/trpc/errorTokens";
import type { ApprovalProcessedStatus } from "../../../bff/src/trpc/errorTokens";

/**
 * BFF が返した tRPC エラーコードの判定。
 *
 * **文面 (message) ではなくコードで見る**のがここの持ち場。message は BFF 側の
 * 事情で変わるが、コードは契約 (ADR 0001: 型は tRPC が真実) なので、片方だけ
 * 変えたときに黙って「未知のエラー」に落ちる経路を作らない。
 *
 * 置き場を api 層の共有ファイルにしているのは、同じ判定が problems.ts と
 * consultation.ts の両方に要るため — 各所で書き直すと、片方だけ判定が
 * ずれても「エラーの種類が変わっただけ」に見えて気づけない。
 */
export function isNotFound(err: unknown): boolean {
  return err instanceof TRPCClientError && err.data?.code === "NOT_FOUND";
}

/**
 * 「承認レコードがもう無い」だけを拾う判定 (#82 / PR #416 Codex 4 巡目 P2)。
 *
 * **`NOT_FOUND` は BFF の意図した応答とは限らない**。tRPC 自身が procedure を
 * 解決できないときにも `NOT_FOUND` を返す
 * (`No procedure found on path "consultation.approve"`) ので、frontend / BFF の
 * 版ずれ・BFF 単独配備・ルーティング事故でも同じコードが飛んでくる。code だけで
 * 「もう受け付けられない」と読むと、**生きている checkpoint を持つ承認カードを
 * 閉じてしまい**、承認も却下も再試行もできなくなる (サーバは承認待ちのまま)。
 *
 * なので BFF がこのケースに限って載せている token との一致まで見る。token の
 * リテラルは BFF の `errorTokens.ts` が 1 個だけ持ち、ここは値として import する
 * (フロントに書き写すと、片側だけ変えたときに黙って判定が外れる)。
 */
export function isApprovalNotFound(err: unknown): boolean {
  return (
    err instanceof TRPCClientError &&
    err.data?.code === "NOT_FOUND" &&
    err.message === APPROVAL_NOT_FOUND_TOKEN
  );
}

/**
 * 「その承認はすでに処理済み」だけを拾い、**結果 (承認済み / 却下済み) まで返す**
 * (#82 / PO 裁定 2026-08-15 B 案)。該当しなければ null。
 *
 * `isApprovalNotFound` と分ける理由: 「もう無い」は消えた理由すら分からないが、
 * こちらは**どちらの決定を受け付けたか**が分かる (`rejected` なら未実行と言い切れる。
 * `approved` は「実行してよいと受け付けた」であって完了の証拠ではない /
 * PR #430 Codex P1)。同じ扱いに戻すと、却下済みまで「実行されたか分かりません」に落ちる。
 *
 * **code だけで判定しない**のも `isApprovalNotFound` と同じ理由 — `CONFLICT` は将来
 * ほかの procedure でも使いうる汎用コードなので、BFF がこのケースにだけ載せる
 * token との一致まで見る。token の書式 (`token:status`) の符号化・復号は BFF の
 * `errorTokens.ts` が 1 箇所で持ち、ここは復号だけを値として import する。
 */
export function approvalAlreadyProcessedStatus(err: unknown): ApprovalProcessedStatus | null {
  if (!(err instanceof TRPCClientError)) return null;
  if (err.data?.code !== "CONFLICT") return null;
  return parseApprovalAlreadyProcessed(err.message);
}
