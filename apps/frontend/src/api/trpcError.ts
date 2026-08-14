import { TRPCClientError } from "@trpc/client";

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
