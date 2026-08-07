/**
 * 下流サービス (ai-agent / voicevox-wrapper) を呼ぶための Managed Identity トークン。
 *
 * 背景 (#86 / ADR 0017): Container Apps 側に組み込み認証 (Entra) の門を置いたので、
 * BFF は「自分が誰か」を証明して通る必要がある。証明手段は **Functions 自身の
 * Managed Identity** で、静的シークレットは増やさない (ADR 0006)。
 *
 * `@azure/identity` は入れない。App Service / Functions のホストは MSI の
 * HTTP エンドポイントを環境変数で渡してくるので、fetch 2 行で足りる。
 * 依存を増やさない方が BFF のコールドスタートにも効く。
 *
 * **ローカル開発はバイパスされる**: `IDENTITY_ENDPOINT` / `IDENTITY_HEADER` は
 * Azure のホストだけが注入する。ローカル (Functions Core Tools) では未設定なので
 * null を返し、呼び出し側は Authorization ヘッダ無しで叩く。ローカルで立てる
 * サービスには門が無いのでこれで通る (ADR 0017 の「ローカル用バイパス」)。
 */

type CachedToken = {
  token: string;
  /** epoch 秒。ここを過ぎたら取り直す (実際にはマージンを引いて判定する)。 */
  expiresOn: number;
};

/** 期限ギリギリのトークンを掴まないためのマージン (秒)。 */
const EXPIRY_MARGIN_SEC = 300;

const cache = new Map<string, CachedToken>();

/**
 * 取得中のリクエストを resource ごとに 1 本に束ねる。
 *
 * これが無いと、キャッシュが空 or 失効した直後に同時到着したリクエストが
 * それぞれ MSI を叩く (コールドスタート明けのバーストで顕著)。キャッシュを
 * 置いた目的の半分 — レイテンシとスロットリングの抑制 — が外れる。
 */
const inFlight = new Map<string, Promise<string>>();

/** テスト用: キャッシュを空にする。 */
export function resetServiceTokenCache(): void {
  cache.clear();
  inFlight.clear();
}

function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * `resource` (= 呼び先の audience) 向けのアクセストークンを取得する。
 *
 * @returns トークン。MSI が使えない環境 (ローカル) では null。
 * @throws MSI エンドポイントはあるのに取得に失敗した場合。**握り潰さない** —
 *         本番でトークンが取れないのは「門の手前で止まっている」状態であり、
 *         黙って無認可リクエストを送ると下流が 401 を返し、原因が分かりにくくなる。
 */
export async function getServiceToken(resource: string): Promise<string | null> {
  const endpoint = process.env["IDENTITY_ENDPOINT"];
  const header = process.env["IDENTITY_HEADER"];

  if (!endpoint || !header) {
    // ローカル開発 (Azure ホスト外)。門が無い前提なので素通しする。
    return null;
  }

  const cached = cache.get(resource);
  if (cached && cached.expiresOn - EXPIRY_MARGIN_SEC > nowSec()) {
    return cached.token;
  }

  // 同じ resource の取得が走っていれば相乗りする。
  const pending = inFlight.get(resource);
  if (pending) return pending;

  const request = fetchToken(endpoint, header, resource).finally(() => {
    // 成否にかかわらず外す。失敗を握ったままだと次のリクエストも同じ例外を貰い続ける。
    inFlight.delete(resource);
  });
  inFlight.set(resource, request);

  return request;
}

async function fetchToken(endpoint: string, header: string, resource: string): Promise<string> {
  const url = `${endpoint}?resource=${encodeURIComponent(resource)}&api-version=2019-08-01`;
  const res = await fetch(url, { headers: { "X-IDENTITY-HEADER": header } });

  if (!res.ok) {
    throw new Error(
      `serviceToken: Managed Identity のトークン取得に失敗しました (resource=${resource}) — ${res.status} ${res.statusText}`,
    );
  }

  const body = (await res.json()) as {
    access_token?: string;
    expires_on?: string | number;
  };

  if (!body.access_token) {
    throw new Error(`serviceToken: access_token が空でした (resource=${resource})`);
  }

  // expires_on は実装によって秒の文字列 / 数値 / 日付文字列で返る。
  // 解釈できないときはキャッシュせず毎回取り直す (誤って長時間使い回すより安全)。
  const parsed = Number(body.expires_on);
  if (Number.isFinite(parsed) && parsed > 0) {
    cache.set(resource, { token: body.access_token, expiresOn: parsed });
  }

  return body.access_token;
}

/**
 * 下流サービス向けの共通ヘッダを作る。
 * audience 未設定 (門を立てていない環境) やローカルでは Authorization を付けない。
 */
export async function serviceHeaders(
  audience: string | undefined,
  base: Record<string, string> = {},
): Promise<Record<string, string>> {
  if (!audience) return base;
  const token = await getServiceToken(audience);
  return token ? { ...base, Authorization: `Bearer ${token}` } : base;
}
