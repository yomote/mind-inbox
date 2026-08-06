/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_VOICEVOX_BASE_URL?: string;
  readonly VITE_VOICEVOX_SPEAKER?: string;
  /** BFF(Functions) のベース URL。SWA Free では別オリジン直叩きなので prod でも必須 (#69)。 */
  readonly VITE_BFF_BASE_URL?: string;
  /** Entra アプリ (SPA) の client ID。tenant ID と揃って初めて認証が有効になる。 */
  readonly VITE_ENTRA_CLIENT_ID?: string;
  /** Entra テナント ID。単一テナント限定。 */
  readonly VITE_ENTRA_TENANT_ID?: string;
  /** BFF 呼び出し用スコープ。既定は api://<clientId>/.default。 */
  readonly VITE_ENTRA_API_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
