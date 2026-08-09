/**
 * Cosmos SDK に渡す `TokenCredential` の薄い shim。
 *
 * なぜ `@azure/identity` を入れないか (ADR 0006 / clients/serviceToken.ts と同じ理由):
 * Functions のホストは Managed Identity の HTTP エンドポイントを環境変数で渡してくるので、
 * 既存の `getServiceToken()` (fetch 2 行) をそのまま流用できる。`@azure/identity` は
 * 依存が重くコールドスタートに効くうえ、MSI を叩く経路が二重になる。
 *
 * Cosmos SDK (`@azure/cosmos@4.10.0`) は `bearerTokenAuthenticationPolicy` 経由で
 * `getToken([scope])` を呼ぶ。scope は既定で `${endpoint}/.default`
 * (= `https://<account>.documents.azure.com/.default`)。MSI の `resource` パラメータは
 * scope ではなく **リソース URI** なので、末尾の `/.default` を落として渡す。
 *
 * テナントによっては account 個別のリソース URI が AAD に無く AADSTS500011 になる。
 * SDK 自身も同じ理由で `https://cosmos.azure.com/.default` へフォールバックするが、
 * SDK 側の判定は例外メッセージに `AADSTS500011` が載っていることが条件で、MSI の
 * HTTP エラーには載らない。そのためフォールバックはここで持つ。
 */
import { getServiceToken } from "../clients/serviceToken";

/** Cosmos DB のテナント共通リソース URI (SDK の AAD_DEFAULT_SCOPE と同じもの)。 */
export const COSMOS_FALLBACK_RESOURCE = "https://cosmos.azure.com";

/** `@azure/core-auth` の TokenCredential のうち、Cosmos SDK が実際に使う分だけ。 */
export type AccessTokenLike = {
  token: string;
  /** epoch ミリ秒。SDK のキャッシュ判定に使われる。 */
  expiresOnTimestamp: number;
};

/** scope (`https://.../.default`) を MSI の resource URI に直す。 */
export function scopeToResource(scope: string): string {
  return scope.replace(/\/\.default$/, "");
}

/**
 * JWT の `exp` (epoch 秒) を取り出す。取れなければ null。
 *
 * 署名は検証しない — このトークンは **自分が受け取って自分が送る** ものであり、
 * ここで見たいのは「いつまでキャッシュしてよいか」だけ。検証するのは Cosmos 側。
 */
export function readJwtExpiryMs(token: string): number | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const json = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as {
      exp?: unknown;
    };
    return typeof json.exp === "number" && Number.isFinite(json.exp) ? json.exp * 1000 : null;
  } catch {
    return null;
  }
}

/** exp が読めなかったときの保守的な寿命 (短めに切って取り直させる)。 */
const FALLBACK_LIFETIME_MS = 5 * 60 * 1000;

/**
 * Functions の System Assigned Managed Identity で Cosmos data plane のトークンを取る。
 *
 * `getServiceToken()` は MSI エンドポイントが無い環境 (ローカル) で null を返す。
 * その場合は **黙って無認可で叩かない** — Cosmos は `disableLocalAuth: true` で
 * キー認証を殺してあるので、無認可リクエストは 401 になり原因が分かりにくくなる。
 */
export class ManagedIdentityCosmosCredential {
  async getToken(scopes: string | string[]): Promise<AccessTokenLike> {
    const scope = Array.isArray(scopes) ? (scopes[0] ?? "") : scopes;
    const primary = scopeToResource(scope);

    let token: string | null;
    try {
      token = await getServiceToken(primary);
    } catch (err) {
      // account 個別のリソース URI が引けないテナント向けの保険。
      if (primary === COSMOS_FALLBACK_RESOURCE) throw err;
      token = await getServiceToken(COSMOS_FALLBACK_RESOURCE);
    }

    if (!token) {
      throw new Error(
        "cosmosCredential: Managed Identity が使えない環境で Cosmos に接続しようとしました。" +
          "ローカルでは COSMOS_ENDPOINT を空にして in-memory リポジトリを使ってください " +
          "(ADR 0030 D7)。",
      );
    }

    return {
      token,
      expiresOnTimestamp: readJwtExpiryMs(token) ?? Date.now() + FALLBACK_LIFETIME_MS,
    };
  }
}
