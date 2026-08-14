from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Azure OpenAI (primary)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    # Responses API 用 (Microsoft Agent Framework の全 LLM 呼び出しが使う)。
    # chat completions 用の api-version (旧 azure_openai_api_version) とは**別物**。
    # 2026-08-10、共用していたため /extract が `400 API version not supported` で
    # 常時 500 になっていた。旧設定は SK 除去 (M1-5) で唯一の利用者 (kernel.py) が
    # 消えたため撤去。
    # 値は agent_framework_openai の既定 (DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION) に合わせる。
    azure_openai_responses_api_version: str = "preview"

    # OpenAI (fallback when Azure is not configured)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Set to true in ACA (managed identity); false for local dev with API key
    use_managed_identity: bool = False

    # ── 外向き呼び出しのタイムアウト (Issue #313) ─────────────────────────────
    #
    # 上限が無いと、1 本の遅いリクエストが ACA のワーカー (maxReplicas=3 / cpu 0.5) と
    # Azure OpenAI の TPM 枠を占有し続ける。しかも上流 (Functions) が 230s で切っても
    # 下流は走り続けるので、誰も待っていないトークンを燃やす。
    #
    # 値の根拠:
    # - `llm_request_timeout_seconds` = 60s: 1 回の HTTP 試行の上限。出力は
    #   max_tokens=1024 で頭打ち (agents.py) なので、通常応答は十数秒で終わる。
    #   60s は「遅いが正常」を切らずに、ぶら下がりだけを切る線。
    # - `llm_total_timeout_seconds` = 120s: リトライ込みの 1 回の LLM 呼び出しの実時間上限
    #   (OpenAI SDK は既定でリトライするため、HTTP 側の上限だけでは総時間を縛れない)。
    #   **上流 Functions の 230s より必ず先に切れる**ことが条件 — 先に切れないと
    #   「ブラウザには 230s の無言切断、こちらのログには何も無い」になる。
    # - `llm_stream_idle_timeout_seconds` = 45s: ストリーミングは総時間ではなく
    #   **チャンク間の無音**で測る (長い応答を正常に流し切れるようにするため)。
    #   最初のトークンまでの待ちもこの上限で切る。
    # - `cosmos_request_timeout_seconds` = 20s: 永続化 I/O。ここが詰まると
    #   /chat 全体が詰まるので、LLM より短く切る。
    llm_request_timeout_seconds: float = 60.0
    llm_total_timeout_seconds: float = 120.0
    llm_stream_idle_timeout_seconds: float = 45.0
    cosmos_request_timeout_seconds: int = 20

    # ── LLM に見せるツール (#320 / #82 design-gate の PO 裁定 2) ───────────────
    #
    # **既定は空 = LLM に 1 本もツールを見せない**。tools.py の題材は SK 時代の
    # 受信箱デモのままで、#321 (ツール題材の再定義) の裁定が出るまで実運用の会話に
    # 出すものではない。配管 (#320) と題材 (#321) の着地時期を切り離すためのフラグ。
    #
    # 値の書式 (`LLM_EXPOSED_TOOLS`):
    #   ""            — 1 本も見せない (既定)
    #   "*"           — registry の全ツール
    #   "a,b"         — registry のうち名前が一致するものだけ
    # **未知の名前はエラーにする** — 黙って無視すると「フラグを立てたのに何も起きない」
    # が正常系と区別できなくなる (綴り間違いが静かに通る)。
    llm_exposed_tools: str = ""

    # 1 リクエストで許すツール実行の総回数 / モデル往復の上限。
    # MAF の既定は max_function_calls=None (**無制限**) / max_iterations=40 なので、
    # モデルが暴走したときにトークンと外向き I/O を止めるものが無い。ここで明示する。
    # 上限に達すると MAF は tool_choice="none" に落として応答テキストを作らせる。
    llm_max_function_calls: int = 4
    llm_max_tool_iterations: int = 4

    # Cosmos DB (ADR 0030 / #188) — 会話セッション・承認レコード・MAF checkpoint の永続化。
    # **cosmos_endpoint の有無が分岐点**: 未設定なら従来どおり in-memory で動く
    # (ローカル / テストの既定 = BFF の COSMOS_ENDPOINT と同じ流儀 / ADR 0030 D7)。
    # 認証は Managed Identity のみ — キーは disableLocalAuth: true で殺されている (D3)。
    # コンテナは bicep (bootstrap-core.bicep) が TTL 付きで宣言する。名前がズレると
    # 「デプロイは通るが実行時 404」になるので、既定値は bicep 側の宣言と揃えること。
    cosmos_endpoint: str = ""
    cosmos_database: str = "mindinbox"
    cosmos_sessions_container: str = "sessions"
    cosmos_approvals_container: str = "approvals"
    cosmos_checkpoints_container: str = "checkpoints"

    app_name: str = "mind-inbox-ai-agent"
    log_level: str = "INFO"

    # VOICEVOX stub — HTTP endpoint for future TTS integration
    voicevox_url: str = "http://localhost:50021"
    voicevox_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
