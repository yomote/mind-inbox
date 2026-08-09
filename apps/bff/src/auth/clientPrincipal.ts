/**
 * EasyAuth (Functions 組み込み認証) が渡すユーザー識別の読み取り (ADR 0030 D5)。
 *
 * Functions は認証を通したリクエストに `x-ms-client-principal` を付ける。中身は
 * base64 の JSON:
 *
 * ```json
 * { "auth_typ": "aad", "name_typ": "...", "role_typ": "...",
 *   "claims": [{ "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
 *                "val": "<oid>" }] }
 * ```
 *
 * ここで取るのは **oid** (Entra のテナント内で不変なユーザー ID)。`preferred_username` や
 * `email` は変わりうるのでパーティションキーに使わない。
 *
 * ヘッダが無い / 壊れている場合は `"local"` に落とす。ローカル開発 (EasyAuth なし) と
 * 認証前の疎通確認をそのまま通すため。**今は単一ユーザーなので実運用でも値は 1 種類**だが、
 * 後から `userId` を足すと repository とテスト seed が全部動くので先に切ってある。
 */
import type { HttpRequest } from "@azure/functions";

/** EasyAuth が無い環境 (ローカル / テスト) の userId。 */
export const LOCAL_USER_ID = "local";

/** oid を載せてくるクレーム名 (長い URI 形式と短い形式の両方が来うる)。 */
const OID_CLAIM_TYPES = [
  "http://schemas.microsoft.com/identity/claims/objectidentifier",
  "oid",
];

/** oid が取れないときの次善 (sub / nameidentifier)。 */
const SUBJECT_CLAIM_TYPES = [
  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
  "sub",
];

type ClientPrincipal = {
  claims?: { typ?: unknown; val?: unknown }[];
};

function pickClaim(principal: ClientPrincipal, types: string[]): string | null {
  for (const type of types) {
    const hit = principal.claims?.find((c) => c.typ === type);
    if (typeof hit?.val === "string" && hit.val.length > 0) return hit.val;
  }
  return null;
}

/**
 * `x-ms-client-principal` ヘッダの値 (base64 JSON) から userId を取る。
 *
 * @returns oid → sub の順で最初に見つかった値。取れなければ `"local"`。
 */
export function userIdFromClientPrincipalHeader(header: string | null | undefined): string {
  if (!header) return LOCAL_USER_ID;
  let principal: ClientPrincipal;
  try {
    principal = JSON.parse(Buffer.from(header, "base64").toString("utf8")) as ClientPrincipal;
  } catch {
    // 壊れたヘッダで 500 にはしない。識別できないなら local と同じ扱いにする。
    return LOCAL_USER_ID;
  }
  return pickClaim(principal, OID_CLAIM_TYPES) ?? pickClaim(principal, SUBJECT_CLAIM_TYPES) ?? LOCAL_USER_ID;
}

/** リクエストから userId を解決する。 */
export function resolveUserId(req: Pick<HttpRequest, "headers"> | undefined): string {
  // req.headers はテスト用のダミー ctx では欠けていることがある。
  const raw = req?.headers?.get?.("x-ms-client-principal");
  return userIdFromClientPrincipalHeader(raw);
}
