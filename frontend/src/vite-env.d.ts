/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_VOICEVOX_BASE_URL?: string;
  readonly VITE_VOICEVOX_SPEAKER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
